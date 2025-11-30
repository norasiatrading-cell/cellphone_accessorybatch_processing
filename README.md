# Data Processor with Groq API

A powerful data processing tool that uses the Groq API to extract features and generate bullet points from CSV and Excel product data. The tool processes data asynchronously in batches with rate limiting and comprehensive error handling.

## Features

- **Multi-Format Support**: Supports CSV (.csv), Excel (.xlsx), and legacy Excel (.xls) files
- **Auto-Detection**: Automatically detects and loads supported data files
- **Feature Extraction**: Extracts 5 unique features from product titles using AI
- **Bullet Point Generation**: Creates 5 bullet points from product descriptions
- **SEO Content Creation**: Generates SEO-optimized paragraph descriptions
- **Material Detection**: Identifies materials with full names and abbreviations
- **Color Recognition**: Extracts primary colors from titles or sales attributes
- **Pattern Analysis**: Identifies design patterns from product titles
- **Case Type Classification**: Categorizes case types using predefined values
- **Processing Statistics**: Detailed success/failure tracking and performance metrics
- **Batch Processing**: Processes large files in configurable batches
- **Async Processing**: Uses asyncio for concurrent processing within batches
- **No Rate Limiting**: Maximum speed processing without artificial delays
- **Error Handling**: Comprehensive logging and error recovery
- **Intermediate Saves**: Saves progress every 5 batches to prevent data loss
- **Configurable**: Easy to configure and extend for additional processing

## Installation

1. Clone or download this project
2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up your environment:
```bash
cp .env.example .env
# Edit .env and add your Groq API key
```

## Data Format

Your input file (CSV or Excel) should have these columns:
```
SPU, SKU, Title, Sales attribute 1, MOQ, Description（without HTML format）, Description, Unit Price, Catalogue, Package Length, Package Width, Package Height, Volume Weight, Gross weight, Brand, Pic 1, Pic 2, Pic 3, Pic 4, Pic 5, Pic 6, Pic 7, Pic 8
```

### Supported File Formats:
- **CSV**: `.csv` files with comma separation
- **Excel**: `.xlsx` files (Excel 2007+)
- **Legacy Excel**: `.xls` files (Excel 97-2003)

The processor will add these new columns:
- `Feature_1` to `Feature_5`: Extracted features from titles
- `Bullet_Point_1` to `Bullet_Point_5`: Generated bullet points from descriptions
- `Material`: Extracted material with full names (e.g., "TPU (Thermoplastic Polyurethane)")
- `Color`: Primary color from title or sales attribute
- `Pattern`: Design pattern from title (e.g., Solid, Striped, Floral, etc.)
- `Type_of_Case`: Specific case type from predefined list (Armband, Basic Case, Bumper, etc.)
- `SEO_Description`: SEO-optimized paragraph description (max 1950 characters)

## Usage

### Create Sample Data
Generate sample CSV and Excel files for testing:
```bash
python create_sample_data.py
```

### Quick Test with Groq Connection
Test your Groq API setup:
```bash
python test_groq_connection.py
# Or use the convenient scripts:
./test_groq.bat        # Windows Command Prompt
./test_groq.ps1        # PowerShell
```

### Test with Sample Data
Run with the sample data to test the setup:
```bash
python test_processor.py
```

### Process Your Data
1. Place your data file in the directory (supported: .csv, .xlsx, .xls)
2. The script will auto-detect your file, or you can specify it in `main.py`:
```python
INPUT_FILE = "your_data_file.xlsx"  # Can be .csv, .xlsx, or .xls
```
3. Run the processor:
```bash
python main.py
```

### Auto-Detection Feature
If no specific input file is found, the script will:
1. Search for data files in the current directory
2. Show you available files if multiple are found
3. Let you choose which file to process
4. Automatically detect the file format and load accordingly

### Configuration Options

Edit `main.py` to adjust these settings:

```python
INPUT_FILE = "your_data.xlsx"      # Your input file (CSV/Excel)
BATCH_SIZE = 100                   # Rows per batch
MAX_BATCHES = None                 # Limit batches for testing (None = process all)
```

### Advanced Configuration

Use the `config.py` file for more advanced configurations:

```python
from config import ProductionConfig, TestConfig

# For testing
config = TestConfig()  # Small batches, limited processing

# For production
config = ProductionConfig()  # Optimized for large datasets
```

## File Structure

```
├── main.py                    # Main processing script
├── config.py                  # Configuration classes
├── test_processor.py          # Test script with sample data
├── test_groq_connection.py    # Test Groq API connection
├── create_sample_data.py      # Generate sample CSV/Excel files
├── test_groq.bat             # Windows batch test script
├── test_groq.ps1             # PowerShell test script
├── sample_data.csv           # Sample CSV for testing
├── sample_data.xlsx          # Sample Excel for testing
├── .env.example              # Environment variables template
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

## Output

The processor creates:
- **Final File**: `processed_data_YYYYMMDD_HHMMSS.{ext}` with same format as input (CSV/Excel)
- **Intermediate Files**: `processed_data_intermediate_N.csv` saved every 5 batches (always CSV for speed)
- **Log File**: `processing.log` with detailed processing information

**Format Matching**: If your input is `data.xlsx`, output will be `processed_data_20251024_181500.xlsx`

## Processing Flow

1. **Load CSV**: Reads input CSV file
2. **Batch Creation**: Splits data into configurable batches
3. **Feature Extraction**: For each row, extracts 5 features from the title using Groq AI
4. **Bullet Generation**: Generates 5 bullet points from the description
5. **Rate Limiting**: Manages API requests to stay within limits
6. **Progress Tracking**: Shows progress bars and logs status
7. **Save Results**: Combines all processed batches into final CSV

## API Usage

The tool uses Groq's `meta-llama/llama-4-scout-17b-16e-instruct` model with:
- **Temperature 0.3** for feature extraction (more focused)
- **Temperature 0.4** for bullet points (slightly more creative)  
- **Temperature 0.6** for SEO descriptions (creative writing)
- **Temperature 0.2** for material, color, pattern extraction (very focused)
- **Temperature 0.1** for case type classification (most focused)
- **No rate limiting** for maximum processing speed
- **Concurrent processing** of 4 extraction tasks per row
- **Character limits**: SEO descriptions capped at 1950 characters
- **Automatic retries** with exponential backoff

## Extending the Processor

The code is designed for easy extension. To add new processing functions:

1. **Add a new async method** to `CSVDataProcessor`:
```python
async def extract_keywords(self, text: str) -> List[str]:
    # Your processing logic here
    pass
```

2. **Update the `process_single_row` method**:
```python
async def process_single_row(self, row: pd.Series) -> Dict:
    # Existing tasks
    features_task = self.extract_features_from_title(title)
    bullets_task = self.generate_bullet_points_from_description(description)
    
    # Add new task
    keywords_task = self.extract_keywords(title)
    
    # Wait for all tasks
    features, bullet_points, keywords = await asyncio.gather(
        features_task, bullets_task, keywords_task
    )
    
    # Add to result
    result['keywords'] = keywords
    return result
```

## Error Handling

- **API Errors**: Automatic retries with fallback values
- **Rate Limiting**: Intelligent throttling to prevent API blocks
- **Data Validation**: Ensures output consistency even with API failures
- **Progress Recovery**: Can resume from intermediate files if needed

## Performance

- **Concurrent Processing**: Multiple API calls per batch
- **Batch Processing**: Configurable batch sizes for memory management
- **Rate Limiting**: Prevents API throttling
- **Progress Tracking**: Real-time progress bars and ETA

For a 1000-row CSV with batch size 100:
- **Processing Time**: ~15-20 minutes (depending on API response times)
- **API Calls**: ~2000 calls (2 per row)
- **Memory Usage**: Low (processes in batches)

## Troubleshooting

### Common Issues

1. **API Key Error**: Make sure your `.env` file has the correct `GROQ_API_KEY`
2. **Rate Limiting**: The tool handles this automatically, but very large files may take time
3. **Memory Issues**: Reduce `BATCH_SIZE` if processing very large files
4. **Network Issues**: The tool retries failed requests automatically

### Debug Mode

Enable debug logging by changing the log level in `main.py`:
```python
logging.basicConfig(level=logging.DEBUG)
```

## License

This project is licensed under the MIT License.