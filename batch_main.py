from groq import Groq
from typing import List, Dict, Optional
import pandas as pd
import asyncio
import json
import os
import logging
import re
import time
import signal
import sys
import shutil
from datetime import datetime
from pathlib import Path
import uuid
from tqdm import tqdm
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"
MODEL_NAME = "llama-3.3-70b-versatile"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('batch_processing.log'),
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
    return input_ext

def save_dataframe(df: pd.DataFrame, file_path: str) -> None:
    """Save DataFrame in the appropriate format based on file extension"""
    file_ext = Path(file_path).suffix.lower()
    
    if file_ext == '.csv':
        df.to_csv(file_path, index=False)
    elif file_ext in ['.xlsx', '.xls']:
        df.to_excel(file_path, index=False, engine='openpyxl')
    else:
        csv_path = file_path.replace(file_ext, '.csv')
        df.to_csv(csv_path, index=False)
        logger.warning(f"Unknown file format {file_ext}, saved as CSV: {csv_path}")

class BatchDataProcessor:
    """Main class for processing CSV/Excel data with Groq Batch API"""
    
    def __init__(self, api_key: str, max_rows: Optional[int] = None, max_requests_per_batch: int = 10000):
        self.client = Groq(api_key=api_key)
        self.max_rows = max_rows
        self.max_requests_per_batch = max_requests_per_batch  # Groq's safe limit per batch
        
        # Statistics tracking
        self.total_rows = 0
        self.successful_rows = 0
        self.failed_rows = 0
        self.start_time = None
        self.total_batch_requests = 0
        self.status_file = "batch_results.json"
        self.regular_rows_count = 0  # Count of rows being processed (excluding parents)
        
        # Family metadata for variation processing
        self.families = {}
        
        # Batch processing
        self.batch_requests = []
        self.request_id_to_row_mapping = {}
    
    def save_progress(self, status: str, output_file: str = None, estimated_rows_completed: int = None):
        """Save current progress to JSON file for real-time monitoring"""
        # Use estimated rows during batch processing, actual counts after processing
        rows_completed = estimated_rows_completed if estimated_rows_completed is not None else (self.successful_rows + self.failed_rows)
        
        progress_data = {
            "status": status,
            "total_rows": self.regular_rows_count if self.regular_rows_count > 0 else self.total_rows,
            "successful_rows": rows_completed,
            "failed_rows": 0 if estimated_rows_completed is not None else self.failed_rows,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat() if self.start_time else None,
            "last_updated": datetime.now().isoformat()
        }
        
        if output_file:
            progress_data["output_file"] = output_file
        
        try:
            with open(self.status_file, "w") as f:
                json.dump(progress_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving progress: {e}")
        
    def clean_json_content(self, content: str) -> str:
        """Clean content before JSON parsing by removing markdown and extra characters"""
        content = content.replace('```json', '').replace('```', '').replace('```text', '')
        content = content.replace('\n', '').replace('\r', '').strip()
        
        if content.startswith('"') and content.endswith('"') and content.count('"') == 2:
            content = content[1:-1]
        
        json_match = re.search(r'\[.*\]', content)
        if json_match:
            content = json_match.group(0)
        
        return content
    
    def safe_json_parse(self, content: str, default_value: list, context: str = "") -> list:
        """Safely parse JSON with better error handling and fallbacks"""
        try:
            cleaned_content = self.clean_json_content(content)
            result = json.loads(cleaned_content)
            
            if not isinstance(result, list):
                logger.warning(f"Expected list but got {type(result)} for {context}. Converting to list.")
                result = [str(result)] if result else default_value
            
            return result
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode error for {context}: {e}")
            logger.warning(f"Original content: {content[:200]}...")
            logger.warning(f"Cleaned content: {self.clean_json_content(content)[:200]}...")
            
            try:
                # Special handling for bullet points with malformed JSON
                # Pattern: "HEADER": Text goes here,
                # Should be: "HEADER: Text goes here",
                if 'bullet point' in context.lower():
                    # Try to fix the malformed bullet point format
                    # Match pattern: "TEXT": more text, 
                    bullet_pattern = r'"([^"]+)":\s*([^,\]]+)'
                    matches = re.findall(bullet_pattern, content)
                    
                    if matches and len(matches) >= len(default_value):
                        # Combine header and text with proper colon
                        fixed_bullets = [f"{header}: {text.strip()}" for header, text in matches]
                        logger.info(f"Fixed malformed bullet points: {len(fixed_bullets)} found")
                        return fixed_bullets[:len(default_value)]
                
                # Standard fallback: extract quoted strings
                quoted_strings = re.findall(r'"([^"]*)"', content)
                if quoted_strings and len(quoted_strings) >= len(default_value):
                    return quoted_strings[:len(default_value)]
                
                if quoted_strings:
                    return quoted_strings + [""] * (len(default_value) - len(quoted_strings))
                    
            except Exception as fallback_error:
                logger.error(f"Fallback parsing failed for {context}: {fallback_error}")
            
            logger.error(f"Using default values for {context}")
            return default_value
            
        except Exception as e:
            logger.error(f"Unexpected error parsing JSON for {context}: {e}")
            return default_value

    def create_feature_extraction_request(self, description: str, row_id: str) -> Dict:
        """Create batch request for feature extraction"""
        prompt = f"""
        Analyze this product description and extract exactly 5 unique features.
        Focus on key product characteristics that would appeal to customers. Dont include generic characteristics like color, material or pattern.        
        Product Description: "{description}"
        Each feature should not be incomplete, it should make sense. You can use 2-3 words for each feature
        
        Return ONLY a JSON list of exactly 5 features, nothing else.
        Example format: ["Wireless Charging Compatible", "Raised Edges", "Precise Cutout", "Slim Profile", "Comfort Grip"]
        """
        
        return {
            "custom_id": f"features_{row_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 100
            }
        }

    def create_bullet_points_request(self, description: str, row_id: str) -> Dict:
        """Create batch request for bullet point generation"""
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
        7. Do not use underscores "_" in the bullet points.

        Example format:
        [
        "COMPREHENSIVE PROTECTION: Offers 360-Degree Protection Against Drops, Bumps, And Scratches.",
        "WIRELESS CHARGING: Compatible With All Wireless Charging Stations And Devices.",
        "PRECISE CUTOUTS: Allows Easy Access To All Ports, Buttons, And Camera Functions.",
        "SLIM DESIGN: Maintains The Original Sleek Profile Of Your Device.",
        "PREMIUM MATERIALS: Made From High-Quality Materials For Long-Lasting Durability."
        ]
        """
        
        return {
            "custom_id": f"bullets_{row_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 200
            }
        }

    def create_attributes_request(self, title: str, description: str, sales_attribute: str, row_id: str) -> Dict:
        """Create batch request for product attributes extraction"""
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
        Example: "Wallet Leather Galaxy For A07/A06 4G/5G Flip Cover with Strap - Don't Touch My Phone"

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
        - If multiple devices are listed (e.g., "Samsung A56 5G / A36 5G"), include both, joined by /.
        If the device is universal or unspecified, return "Universal" or "Not Specified".

        Return ONLY the JSON object, nothing else.
        """
        
        return {
            "custom_id": f"attributes_{row_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 300
            }
        }

    def create_seo_description_request(self, description: str, title: str, row_id: str) -> Dict:
        """Create batch request for SEO description generation"""
        prompt = f"""
        Rewrite the following product description into 2–3 well-structured paragraphs. 
        Each paragraph should highlight a distinct feature or aspect of the product.

        Product Title: "{title}"
        Original Description: "{description}"

        Requirements:
        1. Divide the text into 2–3 paragraphs, each focusing on a different key feature.
        2. Start each paragraph with a short, ALL-CAPS HEADER followed by a colon.
        3. After the colon, write the rest of the paragraph in normal sentence case. The next paragraph will start on a new line.
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
        
        return {
            "custom_id": f"seo_{row_id}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.6,
                "max_tokens": 400
            }
        }

    def create_variation_type_request(self, sales_attributes: List[str], base_sku: str) -> Dict:
        """Create batch request for variation type determination"""
        valid_attributes = [attr for attr in sales_attributes if attr and str(attr).strip()]
        
        if not valid_attributes:
            return None
        
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
        
        return {
            "custom_id": f"variation_{base_sku}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 50
            }
        }

    def identify_families(self, df: pd.DataFrame) -> Dict[str, Dict]:
        """Lightweight family identification"""
        families = {}
        rows_to_remove = set()
        
        logger.info("Identifying product families...")
        
        for idx, row in df.iterrows():
            sku = str(row.get('SKU', '')).strip().strip('\'"')
            
            match = re.match(r'^(.+?)([A-Z])$', sku)
            
            if match:
                base_sku = match.group(1).strip('\'"')
                variant_letter = match.group(2)
                
                if base_sku not in families:
                    families[base_sku] = {
                        'members': [],
                        'has_a_variant': False,
                        'variation_type': None,
                        'sales_attributes': []
                    }
                
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
        
        # Remove families without 'A' variant
        valid_families = {}
        for base_sku, family_info in families.items():
            if not family_info['has_a_variant']:
                logger.info(f"Marking family {base_sku} for removal - no 'A' variant")
                for member in family_info['members']:
                    rows_to_remove.add(member['index'])
                continue
            
            family_info['is_multi_variant'] = len(family_info['members']) > 1
            valid_families[base_sku] = family_info
            
            logger.debug(f"Family {base_sku}: {len(family_info['members'])} variants, "
                        f"multi-variant: {family_info['is_multi_variant']}")
        
        logger.info(f"Identified {len(valid_families)} valid families, "
                   f"marking {len(rows_to_remove)} rows for removal")
        
        return valid_families, rows_to_remove

    def process_variations(self, df: pd.DataFrame) -> pd.DataFrame:
        """Process variation information and add parent rows"""
        logger.info("Processing variation information...")

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
            
            # Update family member indices
            sku_to_new_index = {}
            for new_idx, row in df.iterrows():
                sku = str(row.get('SKU', '')).strip().strip('\'"')
                sku_to_new_index[sku] = new_idx
            
            for base_sku, family_info in families.items():
                updated_members = []
                for member in family_info['members']:
                    if member['sku'] in sku_to_new_index:
                        member['index'] = sku_to_new_index[member['sku']]
                        updated_members.append(member)
                family_info['members'] = updated_members
        
        # Add parent rows for multi-variant families
        parent_rows_to_add = []
        for base_sku, family_info in families.items():
            if family_info['is_multi_variant']:
                # Find the 'A' variant to use as template
                a_member = next(m for m in family_info['members'] if m['variant'] == 'A')
                a_row = df.loc[a_member['index']]
                
                # Create parent row
                parent_row = pd.Series(index=a_row.index, dtype='object')
                
                # Copy specific fields from a_row
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
                    if col not in fields_to_copy + ['SKU', 'Title', 'Variation relation']:
                        parent_row[col] = ""
                
                parent_rows_to_add.append(parent_row)
                
                # Set child relationships
                for member in family_info['members']:
                    df.at[member['index'], 'Variation relation'] = "Child"
                    df.at[member['index'], 'Parent SKU'] = base_sku
        
        # Add parent rows
        if parent_rows_to_add:
            logger.info(f"Adding {len(parent_rows_to_add)} parent rows")
            for parent_row in parent_rows_to_add:
                df.loc[len(df)] = parent_row
        
        # Store families for later use
        self.families = families
        
        logger.info(f"Variation processing complete. Dataset has {len(df)} rows, "
                   f"{len(families)} families identified")
        
        return df

    def prepare_batch_requests(self, df: pd.DataFrame) -> None:
        """Prepare all batch requests for processing"""
        logger.info("Preparing batch requests...")
        
        self.batch_requests = []
        self.request_id_to_row_mapping = {}
        
        # Process regular rows (not parent rows)
        regular_rows = df[df['Variation relation'] != 'Parent']
        self.regular_rows_count = len(regular_rows)  # Store count of rows being processed
        
        for idx, row in regular_rows.iterrows():
            row_id = str(uuid.uuid4())
            self.request_id_to_row_mapping[row_id] = idx
            
            title = str(row.get('Title', ''))
            description = str(row.get('Description（without HTML format）', ''))
            sales_attribute = str(row.get('Sales attribute 1', ''))
            
            # Clean title
            title = title.replace("Case ", "")
            if title.startswith("For"):
                title = title.replace("For", "Case For", 1).strip()
            elif not title.lower().startswith("case for"):
                title = "Case For " + title.strip()
            
            # Create 4 requests per row
            self.batch_requests.append(self.create_feature_extraction_request(description, row_id))
            self.batch_requests.append(self.create_bullet_points_request(description, row_id))
            self.batch_requests.append(self.create_attributes_request(title, description, sales_attribute, row_id))
            self.batch_requests.append(self.create_seo_description_request(description, title, row_id))
        
        # Add variation type requests for multi-variant families
        for base_sku, family_info in self.families.items():
            if family_info['is_multi_variant'] and family_info['sales_attributes']:
                variation_request = self.create_variation_type_request(family_info['sales_attributes'], base_sku)
                if variation_request:
                    self.batch_requests.append(variation_request)
        
        self.total_batch_requests = len(self.batch_requests)
        logger.info(f"Prepared {self.total_batch_requests} batch requests for {len(regular_rows)} rows")

    def submit_batch_jobs_in_chunks(self) -> List[str]:
        """Submit batch requests in chunks to respect API limits"""
        total_requests = len(self.batch_requests)
        
        if total_requests <= self.max_requests_per_batch:
            # Single batch - use original method
            logger.info(f"Submitting single batch with {total_requests} requests")
            batch_id = self.submit_batch_job()
            return [batch_id]
        
        # Split into multiple batches
        num_batches = (total_requests + self.max_requests_per_batch - 1) // self.max_requests_per_batch
        logger.info(f"Splitting {total_requests} requests into {num_batches} batches (max {self.max_requests_per_batch} per batch)")
        
        batch_ids = []
        for batch_num in range(num_batches):
            start_idx = batch_num * self.max_requests_per_batch
            end_idx = min((batch_num + 1) * self.max_requests_per_batch, total_requests)
            chunk = self.batch_requests[start_idx:end_idx]
            
            logger.info(f"Submitting batch {batch_num + 1}/{num_batches} with {len(chunk)} requests")
            batch_id = self.submit_single_batch_chunk(chunk, batch_num)
            batch_ids.append(batch_id)
            
            # Small delay between submissions
            if batch_num < num_batches - 1:
                time.sleep(1)
        
        logger.info(f"Successfully submitted {num_batches} batch jobs")
        return batch_ids
    
    def submit_single_batch_chunk(self, requests: List[Dict], batch_num: int) -> str:
        """Submit a single batch chunk to Groq"""
        # Create batch file
        batch_file_path = f"batch_requests_chunk_{batch_num}.jsonl"
        with open(batch_file_path, 'w') as f:
            for request in requests:
                f.write(json.dumps(request) + '\n')
        
        # Upload batch file
        with open(batch_file_path, 'rb') as f:
            batch_file = self.client.files.create(
                file=f,
                purpose='batch'
            )
        
        # Create batch job
        batch_job = self.client.batches.create(
            input_file_id=batch_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h"
        )
        
        logger.info(f"Batch chunk {batch_num} submitted with ID: {batch_job.id}")
        
        # Clean up local file
        os.remove(batch_file_path)
        
        return batch_job.id

    def submit_batch_job(self) -> str:
        """Submit batch job to Groq"""
        logger.info("Submitting batch job to Groq...")
        
        # Create batch file
        batch_file_path = "batch_requests.jsonl"
        with open(batch_file_path, 'w') as f:
            for request in self.batch_requests:
                f.write(json.dumps(request) + '\n')
        
        # Upload batch file
        with open(batch_file_path, 'rb') as f:
            batch_file = self.client.files.create(
                file=f,
                purpose='batch'
            )
        
        # Create batch job
        batch_job = self.client.batches.create(
            input_file_id=batch_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h"
        )
        
        logger.info(f"Batch job submitted with ID: {batch_job.id}")
        
        # Clean up local file
        os.remove(batch_file_path)
        
        return batch_job.id

    def wait_for_multiple_batch_completions(self, batch_job_ids: List[str]) -> List[Dict]:
        """Wait for multiple batch jobs to complete and return all results"""
        logger.info(f"Waiting for {len(batch_job_ids)} batch jobs to complete...")
        self.save_progress(f"Waiting for {len(batch_job_ids)} batch jobs")
        
        completed_jobs = []
        start_time = time.time()
        
        for idx, batch_job_id in enumerate(batch_job_ids):
            logger.info(f"Processing batch {idx + 1}/{len(batch_job_ids)}: {batch_job_id}")
            self.save_progress(f"Waiting for batch {idx + 1}/{len(batch_job_ids)}")
            
            batch_job = self.wait_for_batch_completion(batch_job_id)
            completed_jobs.append(batch_job)
            
            logger.info(f"Batch {idx + 1}/{len(batch_job_ids)} completed")
        
        elapsed_time = (time.time() - start_time) / 60
        logger.info(f"All {len(batch_job_ids)} batches completed in {elapsed_time:.1f} minutes")
        
        return completed_jobs

    def wait_for_batch_completion(self, batch_job_id: str) -> Dict:
        """Wait for a single batch job to complete and return results"""
        logger.info(f"Waiting for batch job {batch_job_id} to complete...")
        
        start_time = time.time()
        last_status_log = 0
        check_interval = 3  # Check every 3 seconds for more responsive updates
        
        while True:
            batch_job = self.client.batches.retrieve(batch_job_id)
            
            current_time = time.time()
            
            # Update progress with batch API data
            progress_updated = False
            if hasattr(batch_job, 'request_counts') and batch_job.request_counts:
                counts = batch_job.request_counts
                total = getattr(counts, 'total', 0)
                completed_count = getattr(counts, 'completed', 0)
                failed_count = getattr(counts, 'failed', 0)
                
                # Calculate progress based on request completion
                if total > 0:
                    # Calculate request completion percentage
                    requests_completed = completed_count + failed_count
                    request_progress_pct = (requests_completed / total) * 100
                    
                    # Estimate rows completed based on request progress
                    # We use regular_rows_count instead of total_rows to avoid counting parent rows
                    estimated_rows = int((requests_completed / total) * self.regular_rows_count)
                    
                    # Save progress with request-based percentage
                    self.save_progress(
                        f"Processing batch: {request_progress_pct:.1f}% ({requests_completed}/{total} requests)",
                        estimated_rows_completed=estimated_rows
                    )
                    progress_updated = True
            
            # Fallback: Update progress even without request counts to keep UI responsive
            if not progress_updated:
                elapsed = (current_time - start_time) / 60
                self.save_progress(f"Waiting for batch processing (elapsed: {elapsed:.1f} min)", estimated_rows_completed=0)
                logger.debug(f"Progress update: No request counts yet, elapsed {elapsed:.1f} min")
            
            # Log detailed status every 60 seconds
            if current_time - last_status_log >= 60:
                elapsed_time = (current_time - start_time) / 60
                logger.info(f"Batch job status: {batch_job.status} (elapsed: {elapsed_time:.1f} minutes)")
                
                if hasattr(batch_job, 'request_counts') and batch_job.request_counts:
                    counts = batch_job.request_counts
                    # Access attributes directly, not as dictionary
                    total = getattr(counts, 'total', 0)
                    completed = getattr(counts, 'completed', 0) + getattr(counts, 'failed', 0)
                    if total > 0:
                        progress = (completed / total) * 100
                        logger.info(f"Progress: {completed}/{total} requests ({progress:.1f}%)")
                
                last_status_log = current_time
            
            if batch_job.status == 'completed':
                elapsed_time = (current_time - start_time) / 60
                logger.info(f"Batch job completed in {elapsed_time:.1f} minutes!")
                break
            elif batch_job.status in ['failed', 'expired', 'cancelled']:
                raise Exception(f"Batch job {batch_job.status}: {getattr(batch_job, 'error', 'Unknown error')}")
            
            # Wait before checking again (reduced to 5 seconds for more responsive updates)
            time.sleep(check_interval)
        
        return batch_job

    def download_and_parse_all_results(self, batch_jobs: List[Dict]) -> Dict[str, Dict]:
        """Download and parse results from multiple batch jobs"""
        logger.info(f"Downloading and parsing results from {len(batch_jobs)} batch jobs...")
        
        all_results = {}
        
        for idx, batch_job in enumerate(batch_jobs):
            logger.info(f"Downloading results from batch {idx + 1}/{len(batch_jobs)}: {batch_job.id}")
            results = self.download_and_parse_results(batch_job)
            all_results.update(results)
        
        logger.info(f"Total results parsed: {len(all_results)}")
        return all_results

    def download_and_parse_results(self, batch_job) -> Dict[str, Dict]:
        """Download and parse batch results from a single job"""
        logger.info(f"Downloading results for batch job {batch_job.id}...")
        
        # Download output file
        output_file = self.client.files.content(batch_job.output_file_id)
        
        # Parse results
        results = {}
        failed_requests = []
        
        # Get the text content - .text() is a method
        output_text = output_file.text()
        
        for line in output_text.strip().split('\n'):
            if not line:
                continue
                
            try:
                result = json.loads(line)
                custom_id = result['custom_id']
                
                if result.get('response'):
                    # Successful response
                    content = result['response']['body']['choices'][0]['message']['content']
                    results[custom_id] = {
                        'success': True,
                        'content': content
                    }
                else:
                    # Failed response
                    error = result.get('error', {})
                    results[custom_id] = {
                        'success': False,
                        'error': error.get('message', 'Unknown error')
                    }
                    failed_requests.append(custom_id)
                    
            except Exception as e:
                logger.error(f"Error parsing result line: {e}")
                logger.error(f"Line content: {line[:200]}...")
        
        if failed_requests:
            logger.warning(f"Failed requests in this batch: {len(failed_requests)}")
            for req_id in failed_requests[:10]:  # Show first 10
                logger.warning(f"  - {req_id}")
        
        logger.info(f"Successfully parsed {len(results)} results")
        return results

    def process_batch_results(self, df: pd.DataFrame, batch_results: Dict[str, Dict]) -> pd.DataFrame:
        """Process batch results and update dataframe"""
        logger.info("Processing batch results...")
        
        # Initialize new columns
        for i in range(1, 6):
            df[f'Feature_{i}'] = ""
        for i in range(1,6):
            df[f'Bullet_Point_{i}'] = ""
        
        df['New Title'] = ""
        df['New Title 2'] = ""
        df['Material'] = ""
        df['Color'] = ""
        df['Pattern'] = ""  
        df['Type_of_Case'] = ""
        df['New Description（without HTML format）'] = ""
        df['Compatible Device'] = ""
        df['Dimension Unit'] = "Centimetres"
        df['Weight Unit'] = "Kilograms"
        
        # Process variation type results first
        for base_sku, family_info in self.families.items():
            if family_info['is_multi_variant']:
                variation_key = f"variation_{base_sku}"
                if variation_key in batch_results and batch_results[variation_key]['success']:
                    content = batch_results[variation_key]['content'].strip()
                    if "color" in content.lower():
                        family_info['variation_type'] = "Color"
                    elif "pattern" in content.lower():
                        family_info['variation_type'] = "Pattern"
                    else:
                        family_info['variation_type'] = "Pattern"  # Default
                    logger.debug(f"Family {base_sku} variation type: {family_info['variation_type']}")
                else:
                    family_info['variation_type'] = "Pattern"  # Default on failure
        
        # Process row results
        for row_id, row_idx in tqdm(self.request_id_to_row_mapping.items(), desc="Processing results"):
            try:
                row = df.loc[row_idx]
                
                # Get original data
                title = str(row.get('Title', ''))
                title = title.replace("Case ", "")
                if title.startswith("For"):
                    title = title.replace("For", "Case For", 1).strip()
                elif not title.lower().startswith("case for"):
                    title = "Case For " + title.strip()
                
                # Process features
                features_key = f"features_{row_id}"
                if features_key in batch_results and batch_results[features_key]['success']:
                    features = self.safe_json_parse(
                        batch_results[features_key]['content'], 
                        ["", "", "", "", ""], 
                        "feature extraction"
                    )
                    for i, feature in enumerate(features[:5], 1):
                        df.at[row_idx, f'Feature_{i}'] = feature
                else:
                    self.failed_rows += 1
                    logger.warning(f"Failed to get features for row {row_idx}")
                
                # Process bullet points
                bullets_key = f"bullets_{row_id}"
                if bullets_key in batch_results and batch_results[bullets_key]['success']:
                    raw_content = batch_results[bullets_key]['content']
                    logger.info(f"RAW bullet points response for row {row_idx}:")
                    logger.info(f"  Content: {raw_content}")
                    logger.info(f"  Length: {len(raw_content)} chars")
                    
                    bullets = self.safe_json_parse(
                        raw_content, 
                        ["", "", "", "", ""], 
                        "bullet point generation"
                    )
                    
                    logger.info(f"PARSED bullet points for row {row_idx}: {bullets}")
                    
                    for i, bullet in enumerate(bullets[:5], 1):
                        df.at[row_idx, f'Bullet_Point_{i}'] = bullet
                else:
                    self.failed_rows += 1
                    logger.warning(f"Failed to get bullet points for row {row_idx}")
                
                # Process attributes
                attributes_key = f"attributes_{row_id}"
                if attributes_key in batch_results and batch_results[attributes_key]['success']:
                    try:
                        content = batch_results[attributes_key]['content'].strip()
                        # Clean JSON content
                        cleaned_content = self.clean_json_content(content)
                        attributes = json.loads(cleaned_content)
                        
                        df.at[row_idx, 'New Title'] = attributes.get('new_title', title)
                        df.at[row_idx, 'New Title 2'] = title
                        df.at[row_idx, 'Material'] = attributes.get('material', 'Not Specified')
                        df.at[row_idx, 'Color'] = attributes.get('color', 'Not Specified')
                        df.at[row_idx, 'Pattern'] = attributes.get('pattern', '')
                        df.at[row_idx, 'Type_of_Case'] = attributes.get('type_of_case', 'Basic Case')
                        df.at[row_idx, 'Compatible Device'] = attributes.get('compatible_device', 'Not Specified')
                        
                        # Validate case type
                        valid_case_types = ["Armband", "Basic Case", "Bumper", "Dry Bag", "Flip", "Pouch", "Square", "Wallet"]
                        if df.at[row_idx, 'Type_of_Case'] not in valid_case_types:
                            df.at[row_idx, 'Type_of_Case'] = "Basic Case"
                            
                    except Exception as e:
                        logger.error(f"Error parsing attributes for row {row_idx}: {e}")
                        df.at[row_idx, 'Material'] = "Error"
                        df.at[row_idx, 'Color'] = "Error"
                        df.at[row_idx, 'Pattern'] = "Error"
                        df.at[row_idx, 'Type_of_Case'] = "Error"
                        df.at[row_idx, 'Compatible Device'] = "Error"
                        self.failed_rows += 1
                else:
                    self.failed_rows += 1
                    logger.warning(f"Failed to get attributes for row {row_idx}")
                
                # Process SEO description
                seo_key = f"seo_{row_id}"
                if seo_key in batch_results and batch_results[seo_key]['success']:
                    content = batch_results[seo_key]['content'].strip()
                    if content.startswith('"') and content.endswith('"'):
                        content = content[1:-1]
                    if len(content) > 1950:
                        content = content[:1947] + "..."
                    df.at[row_idx, 'New Description（without HTML format）'] = content or "No description available."
                else:
                    df.at[row_idx, 'New Description（without HTML format）'] = "No description available."
                    self.failed_rows += 1
                    logger.warning(f"Failed to get SEO description for row {row_idx}")
                
                # Apply family variation logic
                self.apply_family_variation_logic(df, row_idx)
                
                # Process price calculations (skip for parent rows)
                if df.at[row_idx, 'Variation relation'] != 'Parent':
                    self.calculate_prices(df, row_idx)
                else:
                    # For parent rows, set price columns and unit columns to empty
                    price_columns = [
                        'Calculated Weight', 'Our Price', 'Our Price Rounded', 'MRP', 'MRP Rounded',
                        'Minimum Selling Price', 'Minimum Selling Price Rounded', 
                        'Max Selling Price', 'Max Selling Price Rounded',
                        'Business Price', 'Business Price Rounded',
                        'Dimension Unit', 'Weight Unit'
                    ]
                    for col in price_columns:
                        df.at[row_idx, col] = ""
                
                # Process image URLs
                for i in range(1, 9):
                    original_candidates = [f"Pic {i}", f"Pic{i}", f"pic {i}", f"pic{i}"]
                    old_url = ""
                    for candidate in original_candidates:
                        if candidate in df.columns and pd.notna(df.at[row_idx, candidate]):
                            old_url = str(df.at[row_idx, candidate])
                            break
                    
                    if old_url and old_url != "nan":
                        # Transform URL
                        new_url = old_url.replace("https://ae01.alicdn.com/kf/", "https://m.media-amazon.com/images/I/")
                        if ".jpg" in new_url:
                            new_url = new_url.replace(".jpg", ".jpg")
                        elif ".png" in new_url:
                            new_url = new_url.replace(".png", ".jpg")
                        elif ".webp" in new_url:
                            new_url = new_url.replace(".webp", ".jpg")
                        else:
                            new_url += ".jpg"
                        df.at[row_idx, f"Image {i}"] = new_url
                    else:
                        df.at[row_idx, f"Image {i}"] = ""
                
                self.successful_rows += 1
                
            except Exception as e:
                logger.error(f"Error processing row {row_idx}: {e}")
                self.failed_rows += 1
        
        return df

    def apply_family_variation_logic(self, df: pd.DataFrame, row_idx: int) -> None:
        """Apply family variation logic"""
        sku = str(df.at[row_idx, 'SKU']).strip().strip('\'"')
        
        # Check if this is a parent row or child row
        match = re.match(r'^(.+?)([A-Z])$', sku)
        
        # Determine base SKU
        if match:
            base_sku = match.group(1).strip('\'"')
        elif df.at[row_idx, 'Variation relation'] == 'Parent':
            base_sku = sku
        else:
            return
        
        # Find the family
        family_info = self.families.get(base_sku)
        if not family_info:
            return
        
        # If single variant family, variation columns stay blank
        if not family_info['is_multi_variant']:
            return
        
        # Apply variation type and update appropriate column
        variation_type = family_info.get('variation_type', 'Pattern')
        df.at[row_idx, 'Variation type'] = variation_type.upper()
        
        # Update Color or Pattern column based on variation type
        sales_attr = str(df.at[row_idx, 'Sales attribute 1']).strip()
        if variation_type == "Color" and sales_attr:
            df.at[row_idx, 'Color'] = sales_attr
            logger.debug(f"Updated Color for {sku}: {sales_attr}")
        elif variation_type == "Pattern" and sales_attr:
            df.at[row_idx, 'Pattern'] = sales_attr
            logger.debug(f"Updated Pattern for {sku}: {sales_attr}")

    def calculate_prices(self, df: pd.DataFrame, row_idx: int) -> None:
        """Calculate price columns for a row"""
        # Safely convert value to float, return default if conversion fails
        def safe_float(value, default=0.0):
            try:
                if value is None or value == '':
                    return default
                if pd.isna(value) or str(value).lower() in ['nan', 'none']:
                    return default
                return float(str(value).replace(',', ''))
            except (ValueError, TypeError):
                return default
        
        volume_weight = safe_float(df.at[row_idx, "Volume Weight"], 0)
        gross_weight = safe_float(df.at[row_idx, "Gross weight"], 0)
        unit_price = safe_float(df.at[row_idx, "Unit Price "], 0)
        
        print(f"DEBUG - Row {row_idx} processing:")
        print(f"  Volume Weight: {volume_weight}")
        print(f"  Gross Weight: {gross_weight}")
        print(f"  Unit Price: {unit_price}")
        
        df.at[row_idx, 'Calculated Weight'] = max(volume_weight, gross_weight)
        print(f"  Calculated Weight: {df.at[row_idx, 'Calculated Weight']}")
        
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
        our_price_calc = (unit_price * 1.3 * 90 * 5) + (df.at[row_idx, 'Calculated Weight'] * 1000 * 5) + 150
        df.at[row_idx, 'Our Price'] = our_price_calc
        print(f"  Our Price calculation: ({unit_price} * 1.3 * 90 * 5) + ({df.at[row_idx, 'Calculated Weight']} * 1000 * 5) + 150 = {our_price_calc}")
        
        df.at[row_idx, 'Our Price Rounded'] = round_to_nine(df.at[row_idx, 'Our Price'])
        print(f"  Our Price Rounded: {df.at[row_idx, 'Our Price Rounded']}")
        
        df.at[row_idx, 'MRP'] = df.at[row_idx, 'Our Price Rounded'] * 1.4
        df.at[row_idx, 'MRP Rounded'] = round_to_nine(df.at[row_idx, 'MRP'])
        print(f"  MRP: {df.at[row_idx, 'MRP']} -> MRP Rounded: {df.at[row_idx, 'MRP Rounded']}")
        
        df.at[row_idx, 'Minimum Selling Price'] = df.at[row_idx, 'Our Price'] * 0.8
        df.at[row_idx, 'Minimum Selling Price Rounded'] = round_to_nine(df.at[row_idx, 'Minimum Selling Price'])
        print(f"  Min Selling Price: {df.at[row_idx, 'Minimum Selling Price']} -> Rounded: {df.at[row_idx, 'Minimum Selling Price Rounded']}")
        
        df.at[row_idx, 'Max Selling Price'] = df.at[row_idx, 'MRP Rounded'] * 0.9
        df.at[row_idx, 'Max Selling Price Rounded'] = round_to_nine(df.at[row_idx, 'Max Selling Price'])
        print(f"  Max Selling Price: {df.at[row_idx, 'Max Selling Price']} -> Rounded: {df.at[row_idx, 'Max Selling Price Rounded']}")
        
        df.at[row_idx, 'Business Price'] = df.at[row_idx, 'Our Price Rounded'] * 0.95
        df.at[row_idx, 'Business Price Rounded'] = round_to_nine(df.at[row_idx, 'Business Price'])
        print(f"  Business Price: {df.at[row_idx, 'Business Price']} -> Rounded: {df.at[row_idx, 'Business Price Rounded']}")
        print(f"DEBUG - End row {row_idx} processing\n")

    def update_parent_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Update parent rows with data from their 'A' variant children"""
        logger.info("Updating parent rows with 'A' variant data...")
        
        for base_sku, family_info in self.families.items():
            if not family_info['is_multi_variant']:
                continue
                
            # Find parent row
            parent_rows = df[
                (df['SKU'] == base_sku) & 
                (df['Variation relation'] == 'Parent')
            ]
            
            if parent_rows.empty:
                continue
                
            parent_idx = parent_rows.index[0]
            
            # Find 'A' variant child
            a_variant_sku = base_sku + 'A'
            a_rows = df[df['SKU'] == a_variant_sku]
            
            if a_rows.empty:
                continue
                
            a_idx = a_rows.index[0]
            
            # Copy processed data from 'A' variant to parent
            fields_to_copy = [
                'Feature_1', 'Feature_2', 'Feature_3', 'Feature_4', 'Feature_5',
                'Bullet_Point_1', 'Bullet_Point_2', 'Bullet_Point_3', 'Bullet_Point_4', 'Bullet_Point_5',
                'New Title', 'New Title 2', 'New Description（without HTML format）'
            ]
            
            for field in fields_to_copy:
                if field in df.columns:
                    df.at[parent_idx, field] = df.at[a_idx, field]
            
            # Set variation type for parent from family info
            variation_type = family_info.get('variation_type', 'Pattern')
            df.at[parent_idx, 'Variation type'] = variation_type.upper()
            
            # Clear dimension and weight units for parent rows
            df.at[parent_idx, 'Dimension Unit'] = ""
            df.at[parent_idx, 'Weight Unit'] = ""
            
            # Remove sales attribute from titles for parent rows only
            # Strip everything after " - " from New Title
            if 'New Title' in df.columns and pd.notna(df.at[parent_idx, 'New Title']):
                new_title = str(df.at[parent_idx, 'New Title'])
                df.at[parent_idx, 'New Title'] = re.sub(r' - .+$', '', new_title).strip()
            
            # Strip everything after " - " from New Title 2
            if 'New Title 2' in df.columns and pd.notna(df.at[parent_idx, 'New Title 2']):
                new_title_2 = str(df.at[parent_idx, 'New Title 2'])
                df.at[parent_idx, 'New Title 2'] = re.sub(r' - .+$', '', new_title_2).strip()
            
            logger.debug(f"Updated parent {base_sku} with data from child {a_variant_sku}")
        
        return df

    async def process_file(self, input_file: str, output_file: str) -> None:
        """Main function to process the entire CSV/Excel file using batch API"""
        logger.info(f"Starting batch processing of {input_file}")
        
        self.start_time = time.time()
        self.successful_rows = 0
        self.failed_rows = 0
        
        # Save initial progress
        self.save_progress("Initializing")
        
        # Load data
        try:
            df = load_data_file(input_file)
        except Exception as e:
            logger.error(f"Error reading file: {e}")
            raise
        
        # Apply row limits if specified
        if self.max_rows and len(df) > self.max_rows:
            logger.info(f"Limiting processing to {self.max_rows} rows (original: {len(df)})")
            df = df.head(self.max_rows)
        
        # Process variations
        df = self.process_variations(df)
        self.total_rows = len(df)
        self.save_progress("Processing variations")
        
        # Prepare batch requests
        self.prepare_batch_requests(df)
        self.save_progress("Preparing batch requests")
        
        # Submit batch jobs (chunked if necessary)
        batch_job_ids = self.submit_batch_jobs_in_chunks()
        self.save_progress(f"{len(batch_job_ids)} batch job(s) submitted, waiting for completion")
        
        # Wait for all batches to complete
        if len(batch_job_ids) == 1:
            # Single batch - use original method
            batch_job = self.wait_for_batch_completion(batch_job_ids[0])
            completed_jobs = [batch_job]
        else:
            # Multiple batches
            completed_jobs = self.wait_for_multiple_batch_completions(batch_job_ids)
        
        self.save_progress("All batches completed, downloading results")
        
        # Download and parse results from all batches
        batch_results = self.download_and_parse_all_results(completed_jobs)
        with open("batch_results.json", "w") as f:
            json.dump(batch_results, f)
        
        # Process results and update dataframe
        df = self.process_batch_results(df, batch_results)
        self.save_progress("Processing results")
        
        # Update parent rows with 'A' variant data
        df = self.update_parent_rows(df)
        self.save_progress("Updating parent rows")
        
        # Save results
        if not os.path.exists("output"):
            os.makedirs("output")
        
        output_path = f"output/{output_file}"
        save_dataframe(df, output_path)
        self.save_progress("Saving results")
        
        # Calculate and display statistics
        end_time = time.time()
        processing_time = end_time - self.start_time
        success_rate = (self.successful_rows / self.total_rows) * 100 if self.total_rows > 0 else 0
        
        # Save final progress
        self.save_progress("Completed", output_path)
        
        logger.info(f"Processing complete! Results saved to {output_path}")
        logger.info(f"Final dataset has {len(df)} rows and {len(df.columns)} columns")
        
        # Print detailed statistics
        print("\n" + "="*60)
        print("📊 BATCH PROCESSING STATISTICS")
        print("="*60)
        print(f"📈 Total rows processed: {self.total_rows}")
        print(f"✅ Successfully processed: {self.successful_rows}")
        print(f"❌ Failed to process: {self.failed_rows}")
        print(f"🎯 Success rate: {success_rate:.1f}%")
        print(f"⏱️  Total processing time: {processing_time/60:.1f} minutes ({processing_time:.1f} seconds)")
        print(f"🔗 Total batch requests: {self.total_batch_requests}")
        print(f"📁 Output file: {output_path}")
        print("="*60)


def main():
    """Main function to run the batch data processor"""
    
    # Configuration
    try:
        with open('batch_config.json', 'r') as f:
            config = json.load(f)
        
        INPUT_FILE = config.get("INPUT_FILE", "./test_for_vps.xlsx")
        MAX_ROWS = config.get("MAX_ROWS", None)  # None for no limit
        
    except FileNotFoundError:
        logger.error("config.json file not found!")
        print("Please create a config.json file with the following structure:")
        print("""{
    "INPUT_FILE": "variation_sample.xlsx",
    "MAX_ROWS": null
}""")
        return
    except json.JSONDecodeError:
        logger.error("Invalid JSON format in config.json!")
        return
    
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
    
    # Generate output filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    input_ext = get_output_file_extension(INPUT_FILE)
    OUTPUT_FILE = f"batch_processed_data_{timestamp}{input_ext}"
    
    # Create processor with batch size limit
    processor = BatchDataProcessor(
        api_key=api_key,
        max_rows=MAX_ROWS,
        max_requests_per_batch=200# Groq's safe limit (adjust if needed)
    )
    
    print(f"\n🚀 Starting BATCH processing:")
    print(f"   • Input file: {INPUT_FILE}")
    print(f"   • Max rows: {MAX_ROWS if MAX_ROWS else 'No limit'}")
    print(f"   • Max requests per batch: 10,000 (auto-chunking enabled)")
    print(f"   • Cost savings: ~50% compared to real-time API")
    print(f"   • Processing mode: Batch (submit all → wait → get results)")
    print(f"   • Expected batch completion: 5-30 minutes depending on queue")
    print(f"\n💡 Note: Large datasets will be automatically split into multiple batches")
    print(f"🔄 Batch jobs are processed asynchronously by Groq's servers\n")
    
    # Run the processing
    try:
        import asyncio
        asyncio.run(processor.process_file(INPUT_FILE, OUTPUT_FILE))
        
        print(f"\n✅ Batch processing completed successfully!")
        print(f"📁 Output saved to: output/{OUTPUT_FILE}")
        print(f"💰 Cost savings: ~50% compared to real-time processing")
        
    except KeyboardInterrupt:
        print("\n⚠️  Processing interrupted by user (Ctrl+C)")
        print("🚫 Note: Batch jobs cannot be stopped once submitted to Groq")
        print("📋 The batch job will continue processing on Groq's servers")
        
    except Exception as e:
        print(f"\n❌ Processing failed with error: {e}")
        logger.error(f"Processing failed: {e}")
        raise


if __name__ == "__main__":
    main()