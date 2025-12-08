# run_app.py
import os
import sys
from streamlit.web import cli as stcli

if __name__ == '__main__':
    # 1. Get the PORT from Railway (default to 8501 if missing)
    port = os.getenv('PORT', '8501')
    
    # 2. Construct the Streamlit command manually
    # This avoids the "CMD" shell parsing issues in Docker
    sys.argv = [
        "streamlit",
        "run",
        "streamlit_app.py",
        f"--server.port={port}",
        "--server.address=0.0.0.0",
        "--server.headless=true",
        "--server.enableCORS=false", 
        "--server.enableXsrfProtection=false",
        "--server.fileWatcherType=none"
    ]
    
    print(f"--- LAUNCHING STREAMLIT ON PORT {port} ---")
    
    # 3. Run Streamlit
    sys.exit(stcli.main())
