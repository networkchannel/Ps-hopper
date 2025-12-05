from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import re
import os

app = Flask(__name__)

# ✨ CORS FIX - Permet les requêtes depuis n'importe quel domaine
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

GROUP_ID = "35815907"
API_URL = f"https://groups.roblox.com/v2/groups/{GROUP_ID}/wall/posts?sortOrder=Desc&limit=100&cursor="
ROBLOX_COOKIE = os.getenv('ROBLOX_COOKIE', '')

def get_headers():
    return {
        "cookie": ROBLOX_COOKIE,
        "origin": "https://www.roblox.com",
        "referer": "https://www.roblox.com/",
        "sec-ch-ua": '"Chromium";v="142", "Microsoft Edge";v="142", "Not_A Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
    }

def fetch_group_wall_posts(cursor=""):
    url = f"{API_URL}{cursor}" if cursor else API_URL
    response = requests.get(url, headers=get_headers())
    response.raise_for_status()
    return response.json()

def extract_server_links(posts_data):
    server_link_pattern = r'https://www\.roblox\.com/share\?code=[a-f0-9]+&type=Server'
    results = []
    
    if isinstance(posts_data, dict):
        posts = posts_data.get('data', [])
    else:
        posts = posts_data
    
    for post in posts:
        body = post.get('body', '')
        timestamp = post.get('created', '')
        found_links = re.findall(server_link_pattern, body, re.IGNORECASE)
        
        for link in found_links:
            results.append({
                "link": link,
                "timestamp": timestamp
            })
    
    return results

def fetch_all_pages(max_pages=5):
    cursor = ""
    all_posts = []
    page_count = 0
    
    while page_count < max_pages:
        data = fetch_group_wall_posts(cursor)
        
        if data.get('data'):
            all_posts.extend(data['data'])
        
        if not data.get('nextPageCursor'):
            break
        
        cursor = data['nextPageCursor']
        page_count += 1
    
    return all_posts

@app.route('/links', methods=['GET'])
def get_links():
    try:
        pages = request.args.get('pages', default=1, type=int)
        
        if pages <= 0:
            return jsonify({"error": "Pages must be greater than 0"}), 400
        
        if pages == 1:
            data = fetch_group_wall_posts()
            links = extract_server_links(data)
        else:
            all_posts = fetch_all_pages(max_pages=pages)
            links = extract_server_links(all_posts)
        
        return jsonify(links)
    
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Request failed: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    if not ROBLOX_COOKIE:
        print("WARNING: ROBLOX_COOKIE environment variable is not set")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
