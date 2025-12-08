# debug_run.py
import os
import sys
import socketserver
import http.server

# 1. Get the PORT directly from the environment (bypass shell issues)
PORT = int(os.environ.get("PORT", 8501))

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"RAILWAY IS WORKING! Now we can switch back to Streamlit.")

# 2. Force stdout to flush so you see logs in Railway IMMEDIATELY
print(f"--- STARTING DEBUG SERVER ---")
print(f"--- DETECTED PORT: {PORT} ---")
print(f"--- BINDING TO: 0.0.0.0 ---")
sys.stdout.flush()

try:
    # 3. Explicitly bind to empty string "" (which equals 0.0.0.0)
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print("--- SERVER IS LISTENING ---")
        sys.stdout.flush()
        httpd.serve_forever()
except Exception as e:
    print(f"--- CRITICAL ERROR: {e} ---")
    sys.stdout.flush()
