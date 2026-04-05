import os
import requests
import tempfile
from urllib.parse import urlparse

# --- CONFIGURATION ---
# Replace with a valid token from a test user you created in the Django admin
AUTH_TOKEN = "your_test_user_auth_token_here" 
API_ENDPOINT = "http://localhost:8005/api/v1/clips/"

# Seed data: List of dictionaries containing the source URL and metadata
SEED_CLIPS = [
    {
        "url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "title": "Lo-Fi Study Beats",
        "category": "music"
    },
    {
        "url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "title": "High Energy Workout",
        "category": "motivation"
    },
    # Add as many URLs as you need here
]

def download_audio(url):
    """Downloads a file from a URL to a temporary local file."""
    print(f"Downloading {url}...")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # Create a temporary file to hold the audio
        fd, temp_path = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(fd, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return temp_path
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return None

def upload_to_echoflow(file_path, title, category):
    """Posts the audio file to the EchoFlow backend."""
    print(f"Uploading '{title}' to EchoFlow...")
    
    # Check your settings.py to confirm if you are using Token or Bearer auth
    headers = {
        "Authorization": f"Token {AUTH_TOKEN}" 
    }
    
    data = {
        "title": title,
        "category": category
    }
    
    try:
        with open(file_path, 'rb') as audio_file:
            files = {
                "original_file": audio_file
            }
            response = requests.post(API_ENDPOINT, headers=headers, data=data, files=files)
            
            if response.status_code == 202:
                print(f"✅ Success! Clip '{title}' is processing in Celery.")
                print(f"Response: {response.json()}")
            else:
                print(f"❌ Upload Failed. Status: {response.status_code}")
                print(f"Error: {response.text}")
                
    except Exception as e:
         print(f"Error connecting to API: {e}")

def main():
    print("Starting EchoFlow Seeder...")
    for clip in SEED_CLIPS:
        temp_file_path = download_audio(clip["url"])
        
        if temp_file_path:
            upload_to_echoflow(temp_file_path, clip["title"], clip["category"])
            
            # Clean up the temporary file so we don't fill up the hard drive
            os.remove(temp_file_path)
            
    print("Seeding complete. Check your Celery worker logs to watch the AI extraction.")

if __name__ == "__main__":
    main()