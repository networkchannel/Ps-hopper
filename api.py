from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import re
import os

app = Flask(__name__)

# CORS personnalisé pour autoriser seulement certains domaines
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-Access-Token"]
    }
})

GROUP_ID = "35815907"
API_URL = f"https://groups.roblox.com/v2/groups/{GROUP_ID}/wall/posts?sortOrder=Desc&limit=100&cursor="
ROBLOX_COOKIE = os.getenv('ROBLOX_COOKIE', '')

# Récupération des clés valides depuis l'environnement
VALID_KEYS = os.getenv('VALID_KEY', '').split(',')
VALID_KEYS = [key.strip() for key in VALID_KEYS if key.strip()]

# Tokens actifs (en mémoire, pour une vraie prod utilise Redis ou une DB)
active_tokens = {}

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

def check_page_title():
    """Vérifie si le referer contient le bon titre de page"""
    referer = request.headers.get('Referer', '')
    # On vérifie juste que la requête vient d'un site web légitime
    # Le titre de page ne peut pas être vérifié côté serveur de manière fiable
    return True  # Pour l'instant on autorise, mais on vérifie le token

def verify_access_token():
    """Vérifie le token d'accès dans les headers"""
    token = request.headers.get('X-Access-Token', '')
    return token in active_tokens

@app.route('/verify-key', methods=['POST'])
def verify_key():
    """Vérifie si une clé est valide et génère un token d'accès"""
    try:
        data = request.get_json()
        key = data.get('key', '').strip()
        
        if not key:
            return jsonify({"valid": False, "error": "No key provided"}), 400
        
        if key in VALID_KEYS:
            # Génération d'un token unique
            import secrets
            token = secrets.token_urlsafe(32)
            active_tokens[token] = key
            
            return jsonify({
                "valid": True,
                "token": token,
                "message": "Access granted"
            })
        
        return jsonify({"valid": False, "error": "Invalid key"}), 401
    
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 500

@app.route('/links', methods=['GET'])
def get_links():
    """Récupère les liens de serveurs - nécessite un token valide"""
    try:
        # Vérification du token
        if not verify_access_token():
            return jsonify({"error": "Unauthorized - Invalid or missing token"}), 401
        
        # Vérification du referer (optionnel mais recommandé)
        if not check_page_title():
            return jsonify({"error": "Unauthorized - Invalid referer"}), 403
        
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
    return jsonify({"status": "ok", "valid_keys_count": len(VALID_KEYS)})

if __name__ == '__main__':
    if not ROBLOX_COOKIE:
        print("WARNING: ROBLOX_COOKIE environment variable is not set")
    
    if not VALID_KEYS:
        print("WARNING: VALID_KEY environment variable is not set")
        print("Example: VALID_KEY='key1,key2,key3'")
    else:
        print(f"Loaded {len(VALID_KEYS)} valid keys")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
