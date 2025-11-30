import asyncio
import pandas as pd
import os
import signal
import sys
from groq import AsyncGroq
from typing import List, Dict, Optional
import json
import logging
from datetime import datetime
from pathlib import Path
import time
from tqdm.asyncio import tqdm
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_NAME = "llama-3.3-70b-versatile"

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for more detailed logging
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('processing.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def load_data_file(file_path: str) -> pd.DataFrame:
    """Load data from CSV or Excel file based on file extension"""
    file_path_obj = Path(file_path)
    file_extension = file_path_obj.suffix.lower()
    
    logger.info(f"Loading file: {file_path} (detected format: {file_extension})")
    
    try:
        if file_extension == '.csv':
            df = pd.read_csv(file_path)
            logger.info(f"Successfully loaded CSV file with {len(df)} rows and {len(df.columns)} columns")
        elif file_extension in ['.xlsx', '.xls']:
            # For Excel files, we'll read the first sheet by default
            df = pd.read_excel(file_path, engine='openpyxl' if file_extension == '.xlsx' else 'xlrd')
            logger.info(f"Successfully loaded Excel file with {len(df)} rows and {len(df.columns)} columns")
        else:
            raise ValueError(f"Unsupported file format: {file_extension}. Please use .csv, .xlsx, or .xls files.")
        
        return df
        
    except Exception as e:
        logger.error(f"Error loading file {file_path}: {e}")
        raise

def get_output_file_extension(input_file: str) -> str:
    """Determine output file extension based on input file"""
    input_ext = Path(input_file).suffix.lower()
    # Maintain same format as input file
    return input_ext

def save_dataframe(df: pd.DataFrame, file_path: str) -> None:
    """Save DataFrame in the appropriate format based on file extension"""
    file_ext = Path(file_path).suffix.lower()
    
    if file_ext == '.csv':
        df.to_csv(file_path, index=False)
    elif file_ext in ['.xlsx', '.xls']:
        df.to_excel(file_path, index=False, engine='openpyxl')
    else:
        # Fallback to CSV if unknown format
        csv_path = file_path.replace(file_ext, '.csv')
        df.to_csv(csv_path, index=False)
        logger.warning(f"Unknown file format {file_ext}, saved as CSV: {csv_path}")

class DataProcessor:
    """Main class for processing CSV/Excel data with Groq API"""
    
    def __init__(self, api_key: str, batch_size: int = 100, max_batches: Optional[int] = None, 
                 max_concurrent_requests: int = 10, requests_per_minute: int = 120, 
                 incremental_save: bool = True, incremental_save_interval: int = 10):
        self.client = AsyncGroq(api_key=api_key)
        self.batch_size = batch_size
        self.max_batches = max_batches
        
        # Rate limiting parameters
        self.max_concurrent_requests = max_concurrent_requests
        self.requests_per_minute = requests_per_minute
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.request_times = []  # Track request timestamps for rate limiting
        
        # Statistics tracking
        self.total_rows = 0
        self.successful_rows = 0
        self.failed_rows = 0
        self.start_time = None
        self.total_api_calls = 0
        
        # Family variation processing locks to prevent race conditions
        self.family_locks = {}
        
        # Incremental saving (improved implementation)
        self.incremental_save = incremental_save
        self.incremental_save_interval = incremental_save_interval  # Save every N rows
        self.incremental_output_path = None
        self.incremental_temp_path = None  # Temporary file path for safe writes
        self.processed_rows_count = 0
        self.incremental_buffer = []  # Buffer to hold rows before saving
        self.all_processed_rows = []  # Keep all processed rows for rebuilding Excel
        self.estimated_tokens_used = 0
        
        # Parent row tracking for crash-resistant processing
        self.parent_rows_data = {}  # SKU -> parent row data
        self.pending_parent_updates = set()  # SKUs of parents that need updating
        
        # Family metadata for variation processing
        self.families = {}
    
    async def rate_limited_api_call(self, api_func, *args, **kwargs):
        """Execute API call with rate limiting"""
        async with self.semaphore:
            # Remove timestamps older than 1 minute
            current_time = time.time()
            self.request_times = [t for t in self.request_times if current_time - t < 60]
            
            # If we've hit the rate limit, wait
            if len(self.request_times) >= self.requests_per_minute:
                wait_time = 60 - (current_time - self.request_times[0]) + 1
                logger.info(f"Rate limit reached, waiting {wait_time:.1f} seconds...")
                await asyncio.sleep(wait_time)
                # Clean up timestamps again after waiting
                current_time = time.time()
                self.request_times = [t for t in self.request_times if current_time - t < 60]
            
            # Record this request
            self.request_times.append(current_time)
            self.total_api_calls += 1
            
            # Execute the API call
            try:
                result = await api_func(*args, **kwargs)
                return result
            except Exception as e:
                error_msg = str(e).lower()
                if "rate_limit" in error_msg or "too many requests" in error_msg:
                    logger.warning(f"Rate limit hit, waiting 30 seconds...")
                    await asyncio.sleep(30)
                    # Retry once
                    return await api_func(*args, **kwargs)
                elif any(net_error in error_msg for net_error in [
                    "connection", "network", "timeout", "unreachable", 
                    "failed to resolve", "no internet", "dns", "offline"
                ]):
                    logger.error(f"🌐 Network/Internet connection issue detected: {e}")
                    logger.error("📡 Please check your internet connection and try again.")
                    logger.info("💾 Current progress has been saved to the incremental file.")
                    raise ConnectionError(f"Network connection failed: {e}")
                else:
                    raise e

    def clean_json_content(self, content: str) -> str:
        """Clean content before JSON parsing by removing markdown and extra characters"""
        # Remove common markdown code block markers
        content = content.replace('```json', '').replace('```', '').replace('```text', '')
        
        # Remove newlines and extra whitespace
        content = content.replace('\n', '').replace('\r', '').strip()
        
        # Remove any leading/trailing quotes that might interfere with JSON parsing
        if content.startswith('"') and content.endswith('"') and content.count('"') == 2:
            content = content[1:-1]
        
        # Handle cases where the response might be wrapped in extra formatting
        # Look for the actual JSON array in the response
        import re
        json_match = re.search(r'\[.*\]', content)
        if json_match:
            content = json_match.group(0)
        
        return content
    
    def safe_json_parse(self, content: str, default_value: list, context: str = "") -> list:
        """Safely parse JSON with better error handling and fallbacks"""
        try:
            # First attempt: clean and parse
            cleaned_content = self.clean_json_content(content)
            result = json.loads(cleaned_content)
            
            # Ensure it's a list
            if not isinstance(result, list):
                logger.warning(f"Expected list but got {type(result)} for {context}. Converting to list.")
                result = [str(result)] if result else default_value
            
            return result
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode error for {context}: {e}")
            logger.warning(f"Original content: {content[:200]}...")
            logger.warning(f"Cleaned content: {self.clean_json_content(content)[:200]}...")
            
            # Fallback: try to extract values manually
            try:
                # Try to find quoted strings in the content
                import re
                quoted_strings = re.findall(r'"([^"]*)"', content)
                if quoted_strings and len(quoted_strings) >= len(default_value):
                    return quoted_strings[:len(default_value)]
                
                # If we found some strings but not enough, pad with empty strings
                if quoted_strings:
                    result = quoted_strings[:len(default_value)]
                    while len(result) < len(default_value):
                        result.append("")
                    return result
                    
            except Exception as fallback_error:
                logger.error(f"Fallback parsing failed for {context}: {fallback_error}")
            
            # Final fallback: return default
            logger.error(f"Using default values for {context}")
            return default_value
            
        except Exception as e:
            logger.error(f"Unexpected error parsing JSON for {context}: {e}")
            return default_value
        
    async def extract_features_from_description(self, description: str) -> List[str]:
        """Extract 5 unique features from product description"""
        
        prompt = f"""
        Analyze this product description and extract exactly 5 unique features.
        Focus on key product characteristics that would appeal to customers. Dont include generic characteristics like color, material or pattern.        
        Product Description: "{description}"
        Each feature should not be incomplete, it should make sense. You can use 2-3 words for each feature
        
        Return ONLY a JSON list of exactly 5 features, nothing else.
        Example format: ["Wireless Charging Compatible", "Raised Edges", "Precise Cutout", "Slim Profile", "Comfort Grip"]
        """
        
        try:
            async def make_api_call():
                self.estimated_tokens_used += 300  # Estimate tokens for this call
                return await self.client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=MODEL_NAME,
                    temperature=0.5,
                    max_tokens=100
                )
            
            response = await self.rate_limited_api_call(make_api_call)
            
            content = response.choices[0].message.content.strip()
            logger.debug(f"Features API response: {content[:100]}...")
            
            # Use safe JSON parsing
            features = self.safe_json_parse(content, ["", "", "", "", ""], "feature extraction")
            
            # Ensure we have exactly 5 features
            if len(features) != 5:
                features = features[:5] + [""] * (5 - len(features))
                
            return features
            
        except Exception as e:
            logger.error(f"Error extracting features from description '{description[:50]}...': {e}")
            return ["", "", "", "", ""]
    
    async def generate_bullet_points_from_description(self, description: str) -> List[str]:
        """Generate 5 bullet points from product description"""
        
        prompt = f"""
        Product Description: "{description}"

        Create exactly 5 concise bullet points from this product description.
        Each bullet point should highlight a key feature or benefit that would interest customers.

        Formatting Rules:
        1. Each bullet point must start with a short, descriptive header in ALL CAPS, followed by a colon.
        2. After the colon, write the rest of the text in Title Case (first letter of every word capitalized).
        3. Each bullet should be a clear, factual sentence highlighting one main feature.
        4. Use natural, descriptive language without promotional tone.
        5. Each bullet should be 1–2 sentences long, if needed.
        6. STRICT: Return only a JSON list of exactly 5 bullet points, nothing else.

        Example format:
        [
        "COMPREHENSIVE PROTECTION: Offers 360-Degree Protection Against Drops, Bumps, And Scratches.",
        "WIRELESS CHARGING: Compatible With All Wireless Charging Stations And Devices.",
        "PRECISE CUTOUTS: Allows Easy Access To All Ports, Buttons, And Camera Functions.",
        "SLIM DESIGN: Maintains The Original Sleek Profile Of Your Device.",
        "PREMIUM MATERIALS: Made From High-Quality Materials For Long-Lasting Durability."
        ]
        """

        
        try:
            async def make_api_call():
                self.estimated_tokens_used += 500  # Estimate tokens for this call
                return await self.client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=MODEL_NAME,
                    temperature=0.5,
                    max_tokens=200
                )
            
            response = await self.rate_limited_api_call(make_api_call)
            
            content = response.choices[0].message.content.strip()
            logger.debug(f"Bullet points API response: {content[:100]}...")
            
            # Use safe JSON parsing
            bullet_points = self.safe_json_parse(content, ["", "", "", "", ""], "bullet point generation")
            
            # Ensure we have exactly 5 bullet points
            if len(bullet_points) != 5:
                bullet_points = bullet_points[:5] + [""] * (5 - len(bullet_points))
                
            return bullet_points
            
        except Exception as e:
            logger.error(f"Error generating bullet points from description: {e}")
            return ["", "", "", "", ""]
    
    async def extract_product_attributes(self, title: str, description: str, sales_attribute: str) -> Dict[str, str]:
        """Extract material, color, pattern, case type, and compatible device in a single API call"""
        
        prompt = f"""
        Analyze the product information and extract the following attributes. Return as a JSON object with exactly these keys:

        Product Title: "{title}"
        Product Description: "{description}"
        Sales Attribute: "{sales_attribute}"

        Extract and return in this exact JSON format:
        {{
            "new_title" : "new_title_value",
            "material": "material_name",
            "color": "color_name", 
            "pattern": "pattern_name",
            "type_of_case": "case_type",
            "compatible_device": "device_model"
        }}

        Guidelines:
        
        NEW_TITLE: 
        - Don't start with brand name
        - Max 200 characters
        - Rephrase original title (same info, different flow)
        - Keep all original attributes (synonyms allowed)
        - Don't add new features or promotional words
        - Don't repeat words like "case" or "cover"
        - Format: [Case Type] [Material]"Case" [Device] [Features] - [Pattern]
        Example: "Wallet Leather Galaxy A07/A06 4G/5G Flip Cover with Strap - Don't Touch My Phone"

        MATERIAL: Identify the PRIMARY material with full names and abbreviations where applicable.
        Examples: "TPU (Thermoplastic Polyurethane)", "PC (Polycarbonate)", "PU Leather", "Genuine Leather", "Faux Leather", "Silicone", "Metal", "Glass", "Fabric", "Plastic"
        Default: "Not Specified"

        COLOR: Identify the FULL color, prioritizing sales attribute first.
        - First check sales attribute for color mentions
        - If no color found in sales attribute, then check title and description
        Examples: "Black", "White", "Wine Red", "Blue", "Green", "Transparent Yellow", "Pink", "Purple", "Orange", "Brown", "Grey", "Silver", "Gold", "Rose Gold", "Clear", "Transparent"
        Default: "Not specified"

        PATTERN: Identify design pattern, prioritizing sales attribute first.
        - First check sales attribute for pattern mentions
        - If no pattern found in sales attribute, then check title only.
        - If still not found, leave blank.
        Examples: "Animal", "Print", "Argyle", "Camouflage", "Checkered", "Chevron", "Floral", "Fruits", "Geometric", "Gingham", "Hearts", "Herringbone", "Houndstooth", "Leaves", "Letter", "Marble", "Moire", "Paisley", "Plaid", "Polka Dots", "Solid", "Stars", "Striped", "Tartan", "Tie-Dye"
        Default: "" (empty string if not found)

        TYPE_OF_CASE: Identify case type using ONLY these predefined values:
        "Armband", "Basic Case", "Bumper", "Dry Bag", "Flip", "Pouch", "Square", "Wallet"
        Default: "Basic Case"

        COMPATIBLE_DEVICE:
        - Extract the full compatible device model name(s) (including version and suffixes like FE, 4G, 5G, Plus, Ultra, Global, etc.) from the title or description.
        - If multiple devices are listed (e.g., “Samsung A56 5G / A36 5G”), include both, joined by /.
        If the device is universal or unspecified, return "Universal" or "Not Specified".

        Return ONLY the JSON object, nothing else.
        """
        
        max_retries = 3
        base_delay = 1  # Base delay in seconds
        
        for attempt in range(max_retries):
            try:
                async def make_api_call():
                    self.estimated_tokens_used += 700  # Estimate tokens for this call (increased for new_title)
                    return await self.client.chat.completions.create(
                        messages=[{"role": "user", "content": prompt}],
                        model=MODEL_NAME,
                        temperature=0.2,
                        max_tokens=250  # Increased to accommodate new_title (max 200 chars)
                    )
                
                response = await self.rate_limited_api_call(make_api_call)
                
                content = response.choices[0].message.content.strip()
                logger.debug(f"Attributes API response (attempt {attempt + 1}): {content[:100]}...")
                
                # Try to parse as JSON object (not list)
                try:
                    cleaned_content = self.clean_json_content(content)
                    # For attributes, we expect a JSON object, not a list
                    import re
                    json_match = re.search(r'\{.*\}', cleaned_content)
                    if json_match:
                        cleaned_content = json_match.group(0)
                    
                    attributes = json.loads(cleaned_content)
                    
                    if not isinstance(attributes, dict):
                        raise ValueError(f"Expected dict but got {type(attributes)} for attributes")
                    
                    # Validate that we have at least some required keys
                    required_keys = ["new_title", "material", "color", "pattern", "type_of_case", "compatible_device"]
                    if not any(key in attributes for key in required_keys):
                        raise ValueError("No valid attribute keys found in response")
                        
                except Exception as parse_error:
                    logger.warning(f"Failed to parse attributes JSON (attempt {attempt + 1}): {parse_error}")
                    logger.warning(f"Content was: {content[:200]}...")
                    
                    # If this is not the last attempt, raise to trigger retry
                    if attempt < max_retries - 1:
                        raise parse_error
                    else:
                        # Last attempt failed, use empty dict
                        attributes = {}
                
                # Ensure all required keys exist with defaults
                result = {
                    "new_title": attributes.get("new_title", ""),
                    "material": attributes.get("material", "Not Specified"),
                    "color": attributes.get("color", "Not Specified"),
                    "pattern": attributes.get("pattern", ""),
                    "type_of_case": attributes.get("type_of_case", "Basic Case"),
                    "compatible_device": attributes.get("compatible_device", "Not Specified")
                }
                
                if result['color'] == 'Not Available':
                    print(content)
                
                # Validate case type against predefined values
                valid_case_types = ["Armband", "Basic Case", "Bumper", "Dry Bag", "Flip", "Pouch", "Square", "Wallet"]
                if result["type_of_case"] not in valid_case_types:
                    result["type_of_case"] = "Basic Case"
                
                # If we reached here successfully, return the result
                logger.debug(f"Successfully extracted attributes on attempt {attempt + 1}")
                return result
                
            except Exception as e:
                logger.warning(f"Error extracting product attributes (attempt {attempt + 1}/{max_retries}): {e}")
                
                # If this is not the last attempt, wait and retry
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    # All attempts failed, return default values
                    logger.error(f"All {max_retries} attempts failed for product attributes extraction")
                    return {
                        "new_title": "",
                        "material": "Not Specified",
                        "color": "Not Available", 
                        "pattern": "",
                        "type_of_case": "Basic Case",
                        "compatible_device": "Not Specified"
                    }

    async def generate_seo_description(self, description: str, title: str = "") -> str:
        """Generate SEO-compatible paragraph description from product description"""
        
        prompt = f"""
        Rewrite the following product description into 2–3 well-structured paragraphs. 
        Each paragraph should highlight a distinct feature or aspect of the product.

        Product Title: "{title}"
        Original Description: "{description}"

        Requirements:
        1. Divide the text into 2–3 paragraphs, each focusing on a different key feature.
        2. Start each paragraph with a short, ALL-CAPS HEADER followed by a colon.
        3. After the colon, write the rest of the paragraph in normal sentence case.
        4. Maintain all important product information from the original description.
        5. Improve readability, flow, and factual clarity.
        6. Use natural, descriptive language and present tense, third person.
        7. Avoid promotional or exaggerated claims — keep the tone factual.
        8. STRICT LIMIT: Maximum 1950 characters total.

        Format example:
        "PROTECTIVE CASE: This iPhone case combines durable PC and TPU materials to provide comprehensive protection against drops, scratches, and daily wear while maintaining precise cutouts for easy access to all ports and functions."

        Return ONLY the rewritten description text, with each paragraph starting on a new line.
        No quotes or extra formatting.
        """

        
        try:
            async def make_api_call():
                self.estimated_tokens_used += 800  # Estimate tokens for this call
                return await self.client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=MODEL_NAME,
                    temperature=0.6,  # Slightly higher for more creative writing
                    max_tokens=400   # Generous limit for detailed description
                )
            
            response = await self.rate_limited_api_call(make_api_call)
            
            content = response.choices[0].message.content.strip()
            
            # Remove any quotes that might wrap the content
            if content.startswith('"') and content.endswith('"'):
                content = content[1:-1]
            
            # Ensure character limit
            if len(content) > 1950:
                content = content[:1947] + "..."
            
            return content if content else "No description available."
            
        except Exception as e:
            logger.error(f"Error generating SEO description: {e}")
            return "No description available."
    
    def identify_families(self, df: pd.DataFrame) -> Dict[str, Dict]:
        """Lightweight family identification without AI calls"""
        import re
        
        families = {}
        rows_to_remove = set()
        
        logger.info("Identifying product families...")
        
        for idx, row in df.iterrows():
            sku = str(row.get('SKU', '')).strip().strip('\'"')
            
            # Extract base number and variant letter using regex
            match = re.match(r'^(.+?)([A-Z])$', sku)
            
            if match:
                base_sku = match.group(1).strip('\'"')
                variant_letter = match.group(2)
                
                if base_sku not in families:
                    families[base_sku] = {
                        'members': [],
                        'sales_attributes': [],
                        'variation_type': None,  # To be determined later
                        'has_a_variant': False,
                        'is_multi_variant': False
                    }
                
                # Collect sales attribute
                sales_attr = str(row.get('Sales attribute 1', '')).strip()
                
                families[base_sku]['members'].append({
                    'index': idx,
                    'sku': sku,
                    'variant': variant_letter,
                    'sales_attribute': sales_attr
                })
                
                if sales_attr:
                    families[base_sku]['sales_attributes'].append(sales_attr)
                
                if variant_letter == 'A':
                    families[base_sku]['has_a_variant'] = True
        
        # Determine family types and mark invalid families for removal
        valid_families = {}
        for base_sku, family_info in families.items():
            # Remove families without 'A' variant
            if not family_info['has_a_variant']:
                logger.info(f"Marking family {base_sku} for removal - no 'A' variant")
                for member in family_info['members']:
                    rows_to_remove.add(member['index'])
                continue
            
            # Determine if multi-variant
            family_info['is_multi_variant'] = len(family_info['members']) > 1
            valid_families[base_sku] = family_info
            
            logger.debug(f"Family {base_sku}: {len(family_info['members'])} variants, "
                        f"multi-variant: {family_info['is_multi_variant']}")
        
        logger.info(f"Identified {len(valid_families)} valid families, "
                   f"marking {len(rows_to_remove)} rows for removal")
        
        return valid_families, rows_to_remove
    
    async def determine_variation_type(self, sales_attributes: List[str]) -> str:
        """Determine if variations are based on Color or Pattern using AI"""
        
        # Filter out empty or None values
        valid_attributes = [attr for attr in sales_attributes if attr and str(attr).strip()]
        
        if not valid_attributes:
            return "Pattern"  # Default to Color if no sales attributes
        
        prompt = f"""
**Task:**
Analyze the following list of sales attributes and determine whether their variations are primarily based on **Color** or **Pattern/Design**.

**Sales Attributes:**
{', '.join(valid_attributes)}

**Critical Classification Rules:**

1. **ALWAYS classify as "Pattern" if ANY attribute contains:**
   - **Seasonal references** (Winter, Summer, Spring, Autumn, Fall) - even with colors like "Winter / Blue"
   - **Nature/living things** (Flower, Rose, Lily, Butterfly, Bird, Pigeon, Animal names)
   - **Textures/surfaces** (Marble, Leather, Wood, Galaxy, Glitter, Striped, Dotted)
   - **Descriptive themes** (Cartoon, Vintage, Retro, Abstract, Geometric, Floral)
   - **Objects or concepts** (Star, Moon, Heart, Crown, etc.)
   
2. **Classify as "Color" if ALL attributes are color descriptions:**
   - Simple colors: Red, Blue, Green, Black, White, Silver, Gold, Pink, etc.
   - Compound color names: Wine Red, Sky Blue, Navy Blue, Rose Gold, Space Gray, Midnight Black, Pearl White, etc.
   - Color shades/tones: Light Blue, Dark Green, Bright Red, etc.
   - **No seasonal, thematic, or object-based words** (unless they're standard color modifiers like "Rose" in "Rose Gold")

3. **Special cases like:**
   - "Rose Gold", "Space Gray", "Charcoal Grey", "Creamy yellow", "Coffee" → Color (standard color names)
   - "Winter / Blue", "Summer / Red" → Pattern (seasonal theme)
   - "Butterfly", "Flower", "Marble" → Pattern (design/theme)
   - "DL01", "A1", "B002" → Pattern (unclear codes)

4. **Key test:** Ask yourself: "Are customers choosing based on color preference, or design/theme preference?"
   - If it's about matching a color to their style → Color
   - If it's about choosing a seasonal/thematic design → Pattern

**Examples:**
- "Red, Wine Red, Sky Blue, Black" → Color
- "Winter / Blue, Summer / Red, Autumn / Yellow" → Pattern (seasonal)
- "Butterfly, Flower, Marble" → Pattern (designs)
- "Rose Gold, Space Gray, Midnight Black", "Charcoal Grey", "Gradient Black" → Color (compound color names)
- "Flying Pigeon, Cartoon Bear" → Pattern (objects/themes)

**Output (JSON only, no explanation):**
```json
{{ "variation_type": "Color" }}
```

or
```json
{{ "variation_type": "Pattern" }}
```"""
        logger.info(f"PROMPT for variation type determination:\n{prompt}\n")
        
        try:
            async def make_api_call():
                self.estimated_tokens_used += 200  # Estimate tokens for this call
                return await self.client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=MODEL_NAME,
                    temperature=0.1,  # Low temperature for consistent classification
                    max_tokens=50
                )
            
            response = await self.rate_limited_api_call(make_api_call)
            
            content = response.choices[0].message.content.strip()
            logger.info(f"Variation type API response: {content}")
            # Clean and validate the response
            if "color" in content.lower():
                return "Color"
            elif "pattern" in content.lower():
                return "Pattern"
            else:
                logger.warning(f"Unexpected variation type response: {content}, defaulting to Color")
                return "Pattern"
                
        except Exception as e:
            logger.error(f"Error determining variation type for attributes {valid_attributes}: {e}")
            return "Pattern"  # Default to Pattern on error

    def process_variations(self, df: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, Dict]]:
        """Lightweight variation processing - only family identification, no AI calls"""
        logger.info("Processing variation information (lightweight)...")

        df.sort_values('SKU', inplace=True)
        df.drop_duplicates(['SKU'], inplace=True)
        
        # Initialize new columns
        df['Variation relation'] = ""
        df['Variation type'] = ""
        df['Parent SKU'] = ""
        
        # Identify families and get removal list
        families, rows_to_remove = self.identify_families(df)
        
        # Remove invalid families
        if rows_to_remove:
            logger.info(f"Removing {len(rows_to_remove)} rows from families without 'A' variants")
            df = df.drop(list(rows_to_remove))
            df = df.reset_index(drop=True)
            
            # Update family member indices after row removal and reset
            sku_to_new_index = {}
            for new_idx, row in df.iterrows():
                sku = str(row.get('SKU', '')).strip().strip('\'"')
                sku_to_new_index[sku] = new_idx
            
            # Update all family member indices
            for base_sku, family_info in families.items():
                updated_members = []
                for member in family_info['members']:
                    if member['sku'] in sku_to_new_index:
                        member['index'] = sku_to_new_index[member['sku']]
                        updated_members.append(member)
                family_info['members'] = updated_members
        
        # Add parent rows for multi-variant families
        parent_rows_to_add = []
        import re
        for base_sku, family_info in families.items():
            if family_info['is_multi_variant']:
                # Find the 'A' variant to use as template
                a_member = next(m for m in family_info['members'] if m['variant'] == 'A')
                a_row = df.loc[a_member['index']]
                
                # Create parent row
                parent_row = pd.Series(index=a_row.index, dtype='object')
                
                # Copy only specific fields from a_row
                fields_to_copy = [
                    'Description（without HTML format）', 'Description', 'Feature_1','Feature_2', 'Feature_3',
                    'Feature_4', 'Feature_5', 'Bullet_Point_1', 'Bullet_Point_2', 'Bullet_Point_3',
                    'Bullet_Point_4', 'Bullet_Point_5', 'New Title', 'New Description（without HTML format）'
                ]
                
                for field in fields_to_copy:
                    if field in a_row.index:
                        parent_row[field] = a_row[field]
                
                # Set parent-specific values
                parent_row['SKU'] = base_sku
                parent_row['Title'] = re.sub(r' - .+$', '', str(a_row.get('Title', '')))
                parent_row['Variation relation'] = "Parent"
                
                # Set everything else as blank
                for col in parent_row.index:
                    if col not in fields_to_copy + ['SKU', 'Variation relation'] and pd.isna(parent_row[col]):
                        parent_row[col] = ""
                
                parent_rows_to_add.append(parent_row)
                
                # Set child relationships (without variation type yet)
                for member in family_info['members']:
                    if member['index'] < len(df):  # Check if index still valid after removals
                        try:
                            df.at[member['index'], 'Variation relation'] = "Child"
                            df.at[member['index'], 'Parent SKU'] = base_sku
                        except (KeyError, IndexError):
                            # Index might be invalid after removals, skip
                            pass
        
        # Add parent rows and save them early for crash resistance
        if parent_rows_to_add:
            logger.info(f"Adding {len(parent_rows_to_add)} parent rows")
            for parent_row in parent_rows_to_add:
                df.loc[len(df)] = parent_row
        
        logger.info(f"Lightweight variation processing complete. Dataset has {len(df)} rows, "
                   f"{len(families)} families identified")
        
        return df, families
    
    async def apply_family_variation_logic(self, result: Dict) -> None:
        """Apply family variation logic with lazy AI evaluation"""
        sku = str(result.get('SKU', '')).strip().strip('\'"')
        
        # Check if this is a parent row or child row
        import re
        match = re.match(r'^(.+?)([A-Z])$', sku)
        
        # Determine base SKU
        if match:
            # This is a child row (has variant letter)
            base_sku = match.group(1).strip('\'"')
        elif result.get('Variation relation') == 'Parent':
            # This is a parent row (no variant letter)
            base_sku = sku
        else:
            # Not a family member
            return
        
        # Find the family
        family_info = self.families.get(base_sku)
        if not family_info:
            return  # No family found
        
        # If single variant family, variation columns stay blank
        if not family_info['is_multi_variant']:
            return
        
        # For multi-variant families, determine variation type if not already done
        # Use async lock to prevent race conditions when multiple family members are processed concurrently
        if family_info['variation_type'] is None:
            # Get or create a lock for this family
            if base_sku not in self.family_locks:
                self.family_locks[base_sku] = asyncio.Lock()
            
            async with self.family_locks[base_sku]:
                # Double-check after acquiring lock (another thread might have set it)
                if family_info['variation_type'] is None:
                    logger.info(f"Determining variation type for family {base_sku}")
                    family_info['variation_type'] = await self.determine_variation_type(
                        family_info['sales_attributes']
                    )
                    logger.info(f"Family {base_sku} variation type: {family_info['variation_type']}")
        
        # Apply variation type and update appropriate column
        variation_type = family_info['variation_type']
        result['Variation type'] = variation_type.upper()  # Save in uppercase (COLOR or PATTERN)
        
        # Update Color or Pattern column based on variation type
        sales_attr = str(result.get('Sales attribute 1', '')).strip()
        if variation_type == "Color" and sales_attr:
            result['Color'] = sales_attr
            logger.debug(f"Updated Color for {sku}: {sales_attr}")
        elif variation_type == "Pattern" and sales_attr:
            result['Pattern'] = sales_attr
            logger.debug(f"Updated Pattern for {sku}: {sales_attr}")
    
    async def process_single_row(self, row: pd.Series, skip_ai: bool = False) -> Dict:
        """Process a single row of data"""
        title = str(row.get('Title', ''))
        description = str(row.get('Description（without HTML format）', ''))
        sales_attribute = str(row.get('Sales attribute 1', ''))

        title = title.replace("Case ", "")
        if title.startswith("For"):
            title = title.replace("For", "Case For", 1).strip()
        elif not title.lower().startswith("case for"):
            title = "Case For " + title.strip()
        
        try:
            # Create result dictionary with original data plus new columns
            result = row.to_dict()
            
            if skip_ai:
                # For parent rows, skip AI processing since data is already populated
                # Just ensure the structure is consistent
                if 'New Title' not in result:
                    result['New Title'] = title
                # Ensure all expected keys exist with empty values if missing
                result['New Title 2'] = title
                for i in range(1, 6):
                    if f'Feature_{i}' not in result:
                        result[f'Feature_{i}'] = ""
                    if f'Bullet_Point_{i}' not in result:
                        result[f'Bullet_Point_{i}'] = ""
                for key in ['Material', 'Color', 'Pattern', 'Type_of_Case', 'New Description（without HTML format）', 'Compatible Device']:
                    if key not in result:
                        result[key] = ""
            else:
                # Regular processing with AI calls
                # Create tasks for concurrent processing - now 4 API calls
                features_task = self.extract_features_from_description(description)
                bullets_task = self.generate_bullet_points_from_description(description)
                attributes_task = self.extract_product_attributes(title, description, sales_attribute)
                seo_description_task = self.generate_seo_description(description, title)
                
                # Wait for all tasks to complete
                features, bullet_points, attributes, seo_description = await asyncio.gather(
                    features_task, bullets_task, attributes_task, seo_description_task
                )
                
                # Add feature columns
                for i, feature in enumerate(features, 1):
                    result[f'Feature_{i}'] = feature
                
                # Add bullet point columns
                for i, bullet in enumerate(bullet_points, 1):
                    result[f'Bullet_Point_{i}'] = bullet
                
                # Add new attribute columns from the combined extraction
                result['New Title'] = attributes['new_title'] if attributes['new_title'] else title
                result['New Title 2'] = title
                result['Material'] = attributes['material']
                result['Color'] = attributes['color']
                result['Pattern'] = attributes['pattern']
                result['Type_of_Case'] = attributes['type_of_case']
                result['New Description（without HTML format）'] = seo_description
                result['Compatible Device'] = attributes['compatible_device']
                result['Dimension Unit'] = "Centimeters"
                result['Weight Unit'] = "Kilograms"
            
            import re
            
            # Handle family variation logic with lazy AI evaluation
            await self.apply_family_variation_logic(result)
            
            # Update parent if this is an 'A' variant (crash-resistant parent updating)
            self._update_parent_with_child_data(result)
            
            # Skip price calculations for parent rows
            is_parent_row = result.get('Variation relation') == 'Parent'
            
            if not is_parent_row:
                # Calculate weight with debugging - ensure numeric values
                def safe_float(value, default=0.0):
                    """Safely convert value to float, return default if conversion fails"""
                    try:
                        if value is None or value == '':
                            return default
                        return float(value)
                    except (ValueError, TypeError):
                        return default
                
                volume_weight = safe_float(result.get("Volume Weight", 0))
                gross_weight = safe_float(result.get("Gross weight", 0))
                unit_price = safe_float(result.get("Unit Price " , 0))
                
                print(f"DEBUG - Row processing:")
                print(f"  Volume Weight: {volume_weight}")
                print(f"  Gross Weight: {gross_weight}")
                print(f"  Unit Price: {unit_price}")
                
                result['Calculated Weight'] = max(volume_weight, gross_weight)
                print(f"  Calculated Weight: {result['Calculated Weight']}")
                
                # Helper function to round up to nearest 9
                def round_to_nine(price):
                    price = safe_float(price, 0)
                    if price <= 0:
                        return 0
                    # Get the integer part
                    base = int(price)
                    # If already ends in 9, return as is
                    if base % 10 == 9:
                        return base
                    # Round up to next 9
                    return ((base // 10) * 10) + 9
                
                # Calculate Our Price with debugging
                our_price_calc = (unit_price * 1.3 * 90 * 5) + (result['Calculated Weight'] * 1000 * 5) + 150
                result['Our Price'] = our_price_calc
                print(f"  Our Price calculation: ({unit_price} * 1.3 * 90 * 5) + ({result['Calculated Weight']} * 1000 * 5) + 150 = {our_price_calc}")
                
                result['Our Price Rounded'] = round_to_nine(result['Our Price'])
                print(f"  Our Price Rounded: {result['Our Price Rounded']}")
                
                result['MRP'] = result['Our Price Rounded'] * 1.4
                result['MRP Rounded'] = round_to_nine(result['MRP'])
                print(f"  MRP: {result['MRP']} -> MRP Rounded: {result['MRP Rounded']}")
                
                result['Minimum Selling Price'] = result['Our Price'] * .8
                result['Minimum Selling Price Rounded'] = round_to_nine(result['Minimum Selling Price'])
                print(f"  Min Selling Price: {result['Minimum Selling Price']} -> Rounded: {result['Minimum Selling Price Rounded']}")
                
                result['Max Selling Price'] = result['MRP Rounded'] * .9
                result['Max Selling Price Rounded'] = round_to_nine(result['Max Selling Price'])
                print(f"  Max Selling Price: {result['Max Selling Price']} -> Rounded: {result['Max Selling Price Rounded']}")
                
                result['Business Price'] = result['Our Price Rounded'] * .95
                result['Business Price Rounded'] = round_to_nine(result['Business Price'])
                print(f"  Business Price: {result['Business Price']} -> Rounded: {result['Business Price Rounded']}")
            else:
                # For parent rows, set price columns to empty/blank
                price_columns = [
                    'Calculated Weight', 'Our Price', 'Our Price Rounded', 'MRP', 'MRP Rounded',
                    'Minimum Selling Price', 'Minimum Selling Price Rounded', 
                    'Max Selling Price', 'Max Selling Price Rounded',
                    'Business Price', 'Business Price Rounded'
                ]
                for col in price_columns:
                    result[col] = ""
                print(f"DEBUG - Parent row: Skipped price calculations")
            print(f"DEBUG - End row processing\n")
            for i in range(1, 9):
                # Try common variants of the original pic column name
                original_candidates = [f"Pic {i}", f"Pic{i}", f"pic {i}", f"pic{i}"]
                old_url = ""
                for key in original_candidates:
                    val = result.get(key)
                    if val:
                        old_url = str(val).strip()
                        break

                if not old_url:
                    result[f"Pic {i} new"] = ""
                    continue

                # If already in the target domain, keep as-is
                if "unlogo-img.tvc-mall.com" in old_url:
                    result[f"Pic {i} new"] = old_url
                    continue

                # Extract filename (last path segment)
                m = re.search(r"([^/]+)$", old_url)
                if not m:
                    result[f"Pic {i} new"] = ""
                    continue

                filename = m.group(1)
                # Remove size prefix like "800x800_" if present
                filename = re.sub(r"^\d+x\d+_", "", filename)
                # Remove extension to get the base name
                base = re.sub(r"\.[^.]+$", "", filename)

                # Construct new URL with .jpg and new domain/path
                result[f"Pic {i} new"] = f"http://unlogo-img.tvc-mall.com/uploads/unlogo/details/{base}.jpg"
            # Mark as successful
            self.successful_rows += 1
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing row: {e}")
            # Mark as failed and return original data
            self.failed_rows += 1
            result = row.to_dict()
            
            # Add empty columns for failed rows
            for i in range(1, 6):
                result[f'Feature_{i}'] = ""
                result[f'Bullet_Point_{i}'] = ""
            
            result['Material'] = "Error"
            result['Color'] = "Error"
            result['Pattern'] = "Error"
            result['Type_of_Case'] = "Error"
            result['SEO_Description'] = "Error"
            
            return result
    
    async def process_batch(self, batch_df: pd.DataFrame, batch_num: int, is_parent_batch: bool = False) -> pd.DataFrame:
        """Process a batch of rows asynchronously with optional incremental saving"""
        batch_type = "parent" if is_parent_batch else "original"
        logger.info(f"Processing {batch_type} batch {batch_num} with {len(batch_df)} rows")
        
        # Create tasks for all rows in the batch
        # For parent rows, we pass a flag to skip AI processing since they're already populated
        tasks = [self.process_single_row(row, skip_ai=is_parent_batch) for _, row in batch_df.iterrows()]
        
        # Process all rows in the batch concurrently with progress bar
        results = []
        desc = f"Parent Batch {batch_num}" if is_parent_batch else f"Batch {batch_num}"
        
        if self.incremental_save and self.incremental_output_path:
            # Process with improved incremental saving - save every N rows
            for task in tqdm(asyncio.as_completed(tasks), 
                            total=len(tasks), 
                            desc=desc):
                result = await task
                results.append(result)
                
                # Add to incremental buffer and save when interval is reached
                self._add_to_incremental_buffer(result)
        else:
            # Original batch processing without incremental saves
            for task in tqdm(asyncio.as_completed(tasks), 
                            total=len(tasks), 
                            desc=desc):
                result = await task
                results.append(result)
        
        # Convert results back to DataFrame
        processed_df = pd.DataFrame(results)
        
        logger.info(f"Completed {batch_type} batch {batch_num}")
        
        return processed_df

    def _add_to_incremental_buffer(self, row_result: Dict) -> None:
        """Add processed row to buffer and save when interval is reached"""
        try:
            # Add row to buffer
            self.incremental_buffer.append(row_result)
            self.processed_rows_count += 1
            
            # Save when we reach the interval or it's the first row
            if len(self.incremental_buffer) >= self.incremental_save_interval or self.processed_rows_count == 1:
                self._save_incremental_buffer()
                
        except Exception as e:
            logger.error(f"Error adding to incremental buffer: {e}")

    def _save_incremental_buffer(self) -> None:
        """Save the current buffer to Excel file using safe atomic write - COMBINE with parents"""
        try:
            if not self.incremental_buffer:
                return
            
            # Add buffer rows to the complete list (CHILDREN ONLY)
            self.all_processed_rows.extend(self.incremental_buffer)
            
            # Create combined DataFrame: children + parents
            combined_rows = []
            
            # Add processed children first
            combined_rows.extend(self.all_processed_rows)
            
            # Add current parent data (with any updates from processed 'A' variants)
            for parent_data in self.parent_rows_data.values():
                combined_rows.append(parent_data)
            
            # Create complete DataFrame
            complete_df = pd.DataFrame(combined_rows)
            
            # Write to temporary file first (atomic operation)
            temp_file = self.incremental_temp_path
            complete_df.to_excel(temp_file, index=False, engine='openpyxl')
            
            # Atomically move temp file to final location (prevents corruption)
            import shutil
            shutil.move(temp_file, self.incremental_output_path)
            
            # Create backup every 50 rows
            if self.processed_rows_count % 50 == 0:
                backup_path = self.incremental_output_path.replace('.xlsx', f'_backup_{self.processed_rows_count}.xlsx')
                complete_df.to_excel(backup_path, index=False, engine='openpyxl')
                logger.info(f"Created backup at {backup_path}")
            
            logger.info(f"Incrementally saved {len(self.incremental_buffer)} child rows "
                       f"(total: {len(combined_rows)} rows including parents) to {self.incremental_output_path}")
            
            # Clear the buffer
            self.incremental_buffer.clear()
                
        except Exception as e:
            logger.error(f"Error saving incremental buffer: {e}")
            # Don't clear buffer on error so we can retry
    
    def _save_parent_rows_early(self, parent_rows: pd.DataFrame) -> None:
        """Save parent rows early to prevent loss on crash - SEPARATE from incremental child tracking"""
        try:
            if not self.incremental_save or parent_rows.empty:
                return
                
            logger.info(f"Saving {len(parent_rows)} parent rows early for crash protection...")
            
            # Store parent rows data for later updates (SEPARATE from child tracking)
            for _, parent_row in parent_rows.iterrows():
                parent_sku = str(parent_row.get('SKU', '')).strip().strip('\'"')
                self.parent_rows_data[parent_sku] = parent_row.to_dict()
                self.pending_parent_updates.add(parent_sku)
            
            # Save parents to incremental file immediately (SEPARATE operation)
            if self.parent_rows_data:
                # Create DataFrame with both parents and any existing child data
                combined_rows = []
                
                # Add existing processed children first
                combined_rows.extend(self.all_processed_rows)
                
                # Add parent rows
                for parent_data in self.parent_rows_data.values():
                    combined_rows.append(parent_data)
                
                # Save combined data
                if combined_rows:
                    complete_df = pd.DataFrame(combined_rows)
                    complete_df.to_excel(self.incremental_temp_path, index=False, engine='openpyxl')
                    import shutil
                    shutil.move(self.incremental_temp_path, self.incremental_output_path)
                    logger.info(f"Parent rows saved early to {self.incremental_output_path}")
                
        except Exception as e:
            logger.error(f"Error saving parent rows early: {e}")

    def _update_parent_with_child_data(self, child_result: Dict) -> None:
        """Update parent row when its 'A' variant child is processed"""
        try:
            child_sku = str(child_result.get('SKU', '')).strip().strip('\'"')
            
            # Check if this is an 'A' variant
            if not child_sku.endswith('A'):
                return
                
            # Get parent SKU (remove the 'A')
            parent_sku = child_sku[:-1]
            
            # Check if we have this parent and it needs updating
            if parent_sku not in self.parent_rows_data or parent_sku not in self.pending_parent_updates:
                return
                
            logger.debug(f"Updating parent {parent_sku} with data from child {child_sku}")
            
            # Fields to copy from 'A' variant to parent
            fields_to_copy = [
                'Feature_1', 'Feature_2', 'Feature_3', 'Feature_4', 'Feature_5',
                'Bullet_Point_1', 'Bullet_Point_2', 'Bullet_Point_3', 'Bullet_Point_4', 'Bullet_Point_5',
                'New Title', 'New Description（without HTML format）'
            ]
            
            # Update parent data
            parent_data = self.parent_rows_data[parent_sku]
            for field in fields_to_copy:
                if field in child_result and child_result[field]:
                    parent_data[field] = child_result[field]
            
            # Update parent data in our tracking system (NOT in all_processed_rows)
            # The parent data will be combined with children during incremental saves
            
            # Remove from pending updates
            self.pending_parent_updates.discard(parent_sku)
            logger.debug(f"Parent {parent_sku} updated successfully")
                
        except Exception as e:
            logger.error(f"Error updating parent with child data: {e}")

    def _cleanup_orphaned_parents(self) -> None:
        """Remove parent rows that have no processed children (cleanup for abrupt stops)"""
        try:
            if not self.parent_rows_data:
                return
                
            logger.info("🧹 Cleaning up orphaned parent rows (parents without processed children)...")
            
            # Get all processed child SKUs
            processed_child_skus = set()
            for row_data in self.all_processed_rows:
                sku = str(row_data.get('SKU', '')).strip().strip('\'"')
                processed_child_skus.add(sku)
            
            # Check each parent to see if any of their children were processed
            orphaned_parents = []
            parents_to_keep = {}
            
            for parent_sku, parent_data in self.parent_rows_data.items():
                # Check if this parent has any processed children (A, B, C, etc.)
                has_processed_children = False
                
                # Look for any variant of this parent in processed children
                for child_sku in processed_child_skus:
                    # Check if child_sku belongs to this parent family
                    if child_sku.startswith(parent_sku) and len(child_sku) == len(parent_sku) + 1:
                        # This child belongs to this parent family
                        has_processed_children = True
                        break
                
                if has_processed_children:
                    parents_to_keep[parent_sku] = parent_data
                else:
                    orphaned_parents.append(parent_sku)
            
            # Update parent_rows_data to only keep parents with children
            if orphaned_parents:
                logger.info(f"🗑️  Removing {len(orphaned_parents)} orphaned parent rows:")
                for parent_sku in orphaned_parents:
                    logger.info(f"   • Removed parent: {parent_sku} (no processed children)")
                
                self.parent_rows_data = parents_to_keep
                logger.info(f"✅ Kept {len(parents_to_keep)} parent rows with processed children")
                
                # Print summary for user
                print(f"\n🧹 Data Cleanup Summary:")
                print(f"   • Processed child rows: {len(self.all_processed_rows)}")
                print(f"   • Parent rows with children: {len(parents_to_keep)}")
                print(f"   • Orphaned parents removed: {len(orphaned_parents)}")
                print(f"   • Total clean rows saved: {len(self.all_processed_rows) + len(parents_to_keep)}")
            else:
                logger.info("✅ No orphaned parent rows found - all parents have processed children")
                    
        except Exception as e:
            logger.error(f"Error cleaning up orphaned parents: {e}")

    def _finalize_incremental_save(self) -> None:
        """Save any remaining rows in buffer and perform cleanup"""
        try:
            # Save any remaining rows in buffer
            if self.incremental_buffer:
                self._save_incremental_buffer()
            
            # Clean up orphaned parents (important for abrupt stops)
            self._cleanup_orphaned_parents()
            
            # Final incremental save with cleaned data
            if self.incremental_save and (self.all_processed_rows or self.parent_rows_data):
                combined_rows = []
                combined_rows.extend(self.all_processed_rows)
                for parent_data in self.parent_rows_data.values():
                    combined_rows.append(parent_data)
                
                if combined_rows:
                    complete_df = pd.DataFrame(combined_rows)
                    complete_df.to_excel(self.incremental_temp_path, index=False, engine='openpyxl')
                    import shutil
                    shutil.move(self.incremental_temp_path, self.incremental_output_path)
                    logger.info(f"💾 Final cleaned data saved to {self.incremental_output_path}")
            
            # Clean up temporary file if it exists
            if self.incremental_temp_path and os.path.exists(self.incremental_temp_path):
                os.remove(self.incremental_temp_path)
                logger.info(f"Cleaned up temporary file: {self.incremental_temp_path}")
                    
        except Exception as e:
            logger.error(f"Error finalizing incremental save: {e}")

    async def process_file(self, input_file: str, output_file: str) -> None:
        """Main function to process the entire CSV/Excel file"""
        logger.info(f"Starting processing of {input_file}")
        
        # Initialize statistics
        self.start_time = time.time()
        self.successful_rows = 0
        self.failed_rows = 0
        self.processed_rows_count = 0
        
        # Setup incremental saving if enabled
        if self.incremental_save:
            if os.path.exists("output") == False:
                os.mkdir("output")
            
            # Set up paths for incremental saving
            base_name = output_file.replace('.xlsx', '').replace('.csv', '')
            self.incremental_output_path = f"output/{base_name}_incremental.xlsx"  # Always Excel for client viewing
            self.incremental_temp_path = f"output/{base_name}_incremental_temp.xlsx"  # Temp file for atomic writes
            
            logger.info(f"Incremental saving enabled (every {self.incremental_save_interval} rows):")
            logger.info(f"  • Temp file: {self.incremental_temp_path}")
            logger.info(f"  • Live Excel file: {self.incremental_output_path}")
            
            # Remove existing incremental files if they exist
            for path in [self.incremental_output_path, self.incremental_temp_path]:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"Removed existing file: {path}")
            
            # Initialize buffers (CLEAN SEPARATION)
            self.incremental_buffer = []           # Buffer for current batch
            self.all_processed_rows = []           # ALL processed CHILDREN only
            self.parent_rows_data = {}             # Parent tracking (separate system)
            self.pending_parent_updates = set()   # Parent update tracking
        
        # Read the file (CSV or Excel)
        try:
            df = load_data_file(input_file)
        except Exception as e:
            logger.error(f"Error reading file: {e}")
            raise
        
        # Apply batch limits cleanly for original data
        original_total_rows = len(df)
        
        if self.max_batches:
            target_limit = self.max_batches * self.batch_size
            if len(df) > target_limit:
                df = df.head(target_limit)
                logger.info(f"Limited to {len(df)} rows for batch processing")

        # Process variations BEFORE batch processing (lightweight)
        df, self.families = self.process_variations(df)
        
        # Separate parent rows that were added by variation processing
        original_rows = df[df['Variation relation'] != 'Parent'].copy()
        parent_rows = df[df['Variation relation'] == 'Parent'].copy()
        
        logger.info(f"Split dataset: {len(original_rows)} original rows, {len(parent_rows)} parent rows")
        
        # Save parent rows early for crash resistance
        self._save_parent_rows_early(parent_rows)
        
        # Set total rows for statistics
        self.total_rows = len(df)

        # Calculate batches for original rows only
        total_batches = (len(original_rows) + self.batch_size - 1) // self.batch_size
        if self.max_batches:
            total_batches = min(total_batches, self.max_batches)
        
        logger.info(f"Processing {len(original_rows)} original rows in {total_batches} batches of {self.batch_size}")
        
        processed_batches = []
        
        # Process each batch of original rows
        for i in range(total_batches):
            start_idx = i * self.batch_size
            end_idx = min(start_idx + self.batch_size, len(original_rows))
            batch_df = original_rows.iloc[start_idx:end_idx].copy()
            
            try:
                processed_batch = await self.process_batch(batch_df, i + 1)
                processed_batches.append(processed_batch)
                
                # Save intermediate results every 5 batches
                if (i + 1) % 5 == 0:
                    intermediate_df = pd.concat(processed_batches, ignore_index=True)
                    # Keep intermediate files as CSV for simplicity and speed
                    intermediate_file = f"{output_file}_intermediate_{i+1}.csv"
                    intermediate_df.to_csv(intermediate_file, index=False)
                    logger.info(f"Saved intermediate results to {intermediate_file}")
                    
            except Exception as e:
                logger.error(f"Error processing batch {i + 1}: {e}")
                continue
        
        # Parent rows are now processed incrementally as their 'A' variants complete
        # No need for final parent processing batch - they're already updated and saved
        if self.pending_parent_updates:
            logger.warning(f"Warning: {len(self.pending_parent_updates)} parent rows were not updated "
                          f"(their 'A' variants may not have been processed)")
            logger.warning(f"Pending parent updates: {list(self.pending_parent_updates)}")
        else:
            logger.info("All parent rows were successfully updated with AI data from their 'A' variants")
        
        # Finalize incremental saving if enabled
        if self.incremental_save:
            self._finalize_incremental_save()
        
        # Combine all processed batches + parents (CLEAN COMBINATION)
        if processed_batches:
            # Get processed children
            final_df = pd.concat(processed_batches, ignore_index=True)
            
            # Add processed parent rows from our tracking system (NO DUPLICATION)
            if self.parent_rows_data:
                parent_df_rows = []
                for parent_sku, parent_data in self.parent_rows_data.items():
                    parent_df_rows.append(parent_data)
                
                if parent_df_rows:
                    parent_df = pd.DataFrame(parent_df_rows)
                    final_df = pd.concat([final_df, parent_df], ignore_index=True)
                    logger.info(f"Added {len(parent_df_rows)} parent rows to final output (total: {len(final_df)} rows)")
            
            if os.path.exists("output") == False:
                os.mkdir("output")
            
            # Save in the same format as input file (main output)
            output_path = "output/" + output_file
            save_dataframe(final_df, output_path)
            
            # Calculate and display statistics
            end_time = time.time()
            processing_time = end_time - self.start_time
            success_rate = (self.successful_rows / self.total_rows) * 100 if self.total_rows > 0 else 0
            
            logger.info(f"Processing complete! Results saved to output/{output_file}")
            logger.info(f"Final dataset has {len(final_df)} rows and {len(final_df.columns)} columns")
            
            # Validation: Check for duplicates
            sku_counts = final_df['SKU'].value_counts()
            duplicates = sku_counts[sku_counts > 1]
            if not duplicates.empty:
                logger.warning(f"Found {len(duplicates)} duplicate SKUs in final output:")
                for sku, count in duplicates.items():
                    logger.warning(f"  SKU '{sku}' appears {count} times")
            else:
                logger.info("✅ No duplicate SKUs found in final output")
            
            # Show new columns added
            original_columns = set(df.columns)
            new_columns = set(final_df.columns) - original_columns
            logger.info(f"Added columns: {sorted(new_columns)}")
            
            # Print detailed statistics
            print("\n" + "="*60)
            print("📊 PROCESSING STATISTICS")
            print("="*60)
            print(f"📈 Total rows processed: {self.total_rows}")
            print(f"   • Child rows: {len(final_df) - len(self.parent_rows_data)}")
            print(f"   • Parent rows: {len(self.parent_rows_data)}")
            print(f"✅ Successfully processed: {self.successful_rows}")
            print(f"❌ Failed to process: {self.failed_rows}")
            print(f"🎯 Success rate: {success_rate:.1f}%")
            print(f"⏱️  Total processing time: {processing_time/60:.1f} minutes ({processing_time:.1f} seconds)")
            print(f"⚡ Average time per row: {processing_time/self.total_rows:.2f} seconds")
            print(f"🔗 Total API calls made: {self.total_api_calls}")
            print(f"🪙 Estimated tokens used: {self.estimated_tokens_used:,}")
            print(f"📊 Average tokens per minute: {(self.estimated_tokens_used / (processing_time / 60)):,.0f}")
            print(f"📁 Output file: output/{output_file}")
            print(f"📊 Columns added: {len(new_columns)} ({', '.join(sorted(new_columns))})")
            print("="*60)
            
        else:
            logger.error("No batches were successfully processed")
            print("\n❌ Processing failed - no data was successfully processed")

def main():
    """Main function to run the data processor"""
    
    # Global processor reference for signal handling
    processor = None
    
    def signal_handler(signum, frame):
        """Handle system signals for graceful shutdown"""
        signal_name = signal.Signals(signum).name
        print(f"\n🛑 Received {signal_name} signal - initiating graceful shutdown...")
        logger.info(f"Received {signal_name} signal - performing cleanup...")
        
        if processor:
            try:
                processor._finalize_incremental_save()
                print(f"💾 Progress saved to: {processor.incremental_output_path}")
                print(f"📊 Processed {processor.successful_rows} rows before shutdown")
            except Exception as e:
                logger.error(f"Error during signal cleanup: {e}")
        
        print("👋 Graceful shutdown completed")
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal
    
    # Configuration
    # Load configuration from config.json
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        INPUT_FILE = config.get("INPUT_FILE", "variation_sample.xlsx")
        BATCH_SIZE = config.get("BATCH_SIZE", 100)
        MAX_BATCHES = config.get("MAX_BATCHES", 5)
        MAX_CONCURRENT_REQUESTS = config.get("MAX_CONCURRENT_REQUESTS", 20)
        REQUESTS_PER_MINUTE = config.get("REQUESTS_PER_MINUTE", 1000)
        INCREMENTAL_SAVE_INTERVAL = config.get("INCREMENTAL_SAVE_INTERVAL", 20)
        
    except FileNotFoundError:
        logger.error("config.json file not found!")
        print("Please create a config.json file with the following structure:")
        print("""{
    "INPUT_FILE": "variation_sample.xlsx",
    "BATCH_SIZE": 100,
    "MAX_BATCHES": 5, // null for no limit
    "MAX_CONCURRENT_REQUESTS": 20,
    "REQUESTS_PER_MINUTE": 1000,
    "INCREMENTAL_SAVE_INTERVAL": 20 // Save every N rows
}""")
        return
    except json.JSONDecodeError:
        logger.error("Invalid JSON format in config.json!")
        return

    # Token usage estimate: 4 calls × ~600 tokens average = ~2400 tokens per row
    # With 100 rows per batch × 2400 = 240k tokens per batch
    # This should keep us well under the 300k/minute limit
    
    # Get API key from environment variable
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.error("GROQ_API_KEY environment variable not found!")
        print("Please set your Groq API key in a .env file:")
        print("GROQ_API_KEY=your_api_key_here")
        return
    
    # Validate file format
    file_ext = Path(INPUT_FILE).suffix.lower()
    if file_ext not in ['.csv', '.xlsx', '.xls']:
        logger.error(f"Unsupported file format: {file_ext}")
        print("Supported formats: .csv, .xlsx, .xls")
        return
    
    # Generate output filename with same extension as input
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    input_ext = get_output_file_extension(INPUT_FILE)
    OUTPUT_FILE = f"processed_data_{timestamp}{input_ext}"
    
    # Create processor and run with rate limiting
    processor = DataProcessor(
        api_key=api_key,
        batch_size=BATCH_SIZE,
        max_batches=MAX_BATCHES,
        max_concurrent_requests=MAX_CONCURRENT_REQUESTS,
        requests_per_minute=REQUESTS_PER_MINUTE,
        incremental_save=True,  # Enable incremental saving by default
        incremental_save_interval=INCREMENTAL_SAVE_INTERVAL  # Save every N rows (configurable)
    )
    
    print(f"\n🚀 Starting processing with enhanced error handling:")
    print(f"   • Batch size: {BATCH_SIZE} rows")
    print(f"   • Max concurrent requests: {MAX_CONCURRENT_REQUESTS}")
    print(f"   • Max requests per minute: {REQUESTS_PER_MINUTE}")
    print(f"   • Incremental saving: ENABLED (Excel file updated every {processor.incremental_save_interval} rows)")
    print(f"   • Backup files: Created every 50 rows")
    print(f"   • Client can view live Excel file during processing")
    print(f"   • Crash-resistant: Parent rows saved early and updated incrementally")
    print(f"   • Graceful shutdown: Orphaned parents cleaned up automatically")
    print(f"   • Network error handling: Automatic detection and graceful stop")
    print(f"   • Estimated token usage per row: ~2400 tokens")
    print(f"   • This should stay well under the 300k tokens/minute limit")
    print(f"\n💡 Press Ctrl+C anytime to gracefully stop and save progress\n")
    
    # Run the async processing
    try:
        asyncio.run(processor.process_file(INPUT_FILE, OUTPUT_FILE))
        print(f"\n✅ Processing completed successfully!")
        print(f"📁 Output saved to: output/{OUTPUT_FILE}")
        print(f"🔗 Total API calls made: {processor.total_api_calls}")
        print(f"🪙 Estimated tokens used: {processor.estimated_tokens_used:,}")
        
    except KeyboardInterrupt:
        print("\n⚠️  Processing interrupted by user (Ctrl+C)")
        logger.info("🛑 Processing interrupted by user - performing cleanup...")
        
        # Perform cleanup and save current progress
        try:
            processor._finalize_incremental_save()
            print(f"💾 Current progress saved to: {processor.incremental_output_path}")
            print(f"📊 Processed {processor.successful_rows} rows before interruption")
            print(f"🧹 Cleaned up any orphaned parent rows")
        except Exception as cleanup_error:
            logger.error(f"Error during cleanup: {cleanup_error}")
        
        print("👋 You can restart the script to continue processing remaining data")
        
    except ConnectionError as e:
        print(f"\n🌐 Network Connection Error!")
        logger.error(f"Network issue: {e}")
        
        # Perform cleanup and save current progress
        try:
            processor._finalize_incremental_save()
            print(f"💾 Current progress saved to: {processor.incremental_output_path}")
            print(f"📊 Processed {processor.successful_rows} rows before connection issue")
            print(f"🧹 Cleaned up any orphaned parent rows")
        except Exception as cleanup_error:
            logger.error(f"Error during cleanup: {cleanup_error}")
        
        print("🔄 Please check your internet connection and restart the script")
        
    except Exception as e:
        print(f"\n❌ Processing failed with error: {e}")
        logger.error(f"Processing failed: {e}")
        
        # Attempt cleanup even on unexpected errors
        try:
            if hasattr(processor, '_finalize_incremental_save'):
                processor._finalize_incremental_save()
                print(f"💾 Emergency save completed: {processor.incremental_output_path}")
        except Exception as cleanup_error:
            logger.error(f"Error during emergency cleanup: {cleanup_error}")
        
        raise

if __name__ == "__main__":
    main()