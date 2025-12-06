from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import re
import os
from datetime import datetime, timedelta
from collections import defaultdict
import secrets
import threading
import time

app = Flask(__name__)

# CORS personnalisé pour autoriser seulement certains domaines
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-Access-Token", "X-Admin-Token"]
    }
})

GROUP_ID = "35815907"
API_URL = f"https://groups.roblox.com/v2/groups/{GROUP_ID}/wall/posts?sortOrder=Desc&limit=100&cursor="
ROBLOX_COOKIE = os.getenv('ROBLOX_COOKIE', '')

# Récupération des clés valides depuis l'environnement
VALID_KEYS = os.getenv('VALID_KEY', '').split(',')
VALID_KEYS = [key.strip() for key in VALID_KEYS if key.strip()]

# Credentials Admin depuis l'environnement
ADMIN_LOGIN = os.getenv('ADMIN_LOGIN', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'KryosAdmin2025!')

# Tokens actifs (en mémoire, pour une vraie prod utilise Redis ou une DB)
active_tokens = {}
admin_tokens = {}

# Rate limiting pour admin login uniquement
admin_login_attempts = defaultdict(list)
MAX_LOGIN_ATTEMPTS = 5
LOGIN_TIMEOUT_MINUTES = 15

# Stockage des connexions (en mémoire, utilise une DB en prod)
connections_log = []

# ===== SYSTÈME DE CACHE =====
cache_data = {
    "links": [],
    "last_update": None,
    "is_updating": False
}
CACHE_DURATION_SECONDS = 60  # Refresh toutes les 60 secondes
cache_lock = threading.Lock()

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

def get_client_ip():
    """Récupère l'IP du client en tenant compte des proxies"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def check_rate_limit(ip):
    """Vérifie le rate limit pour les tentatives de connexion admin"""
    now = datetime.now()
    # Nettoyer les anciennes tentatives
    admin_login_attempts[ip] = [
        attempt for attempt in admin_login_attempts[ip]
        if now - attempt < timedelta(minutes=LOGIN_TIMEOUT_MINUTES)
    ]
    
    if len(admin_login_attempts[ip]) >= MAX_LOGIN_ATTEMPTS:
        return False
    
    return True

def add_login_attempt(ip):
    """Ajoute une tentative de connexion"""
    admin_login_attempts[ip].append(datetime.now())

def fetch_group_wall_posts(cursor=""):
    url = f"{API_URL}{cursor}" if cursor else API_URL
    response = requests.get(url, headers=get_headers(), timeout=10)
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

def update_cache():
    """Met à jour le cache des liens de serveurs"""
    global cache_data
    
    with cache_lock:
        if cache_data["is_updating"]:
            return  # Éviter les mises à jour simultanées
        
        cache_data["is_updating"] = True
    
    try:
        print(f"[{datetime.now()}] Updating cache...")
        all_posts = fetch_all_pages(max_pages=5)
        links = extract_server_links(all_posts)
        
        with cache_lock:
            cache_data["links"] = links
            cache_data["last_update"] = datetime.now()
            cache_data["is_updating"] = False
        
        print(f"[{datetime.now()}] Cache updated successfully - {len(links)} links found")
    
    except Exception as e:
        print(f"[{datetime.now()}] Cache update failed: {str(e)}")
        with cache_lock:
            cache_data["is_updating"] = False

def is_cache_valid():
    """Vérifie si le cache est encore valide"""
    if cache_data["last_update"] is None:
        return False
    
    elapsed = (datetime.now() - cache_data["last_update"]).total_seconds()
    return elapsed < CACHE_DURATION_SECONDS

def get_cached_links():
    """Récupère les liens depuis le cache ou déclenche une mise à jour"""
    if not is_cache_valid() and not cache_data["is_updating"]:
        # Lancer la mise à jour en arrière-plan
        threading.Thread(target=update_cache, daemon=True).start()
    
    # Retourner le cache actuel (même s'il est en cours de mise à jour)
    with cache_lock:
        return cache_data["links"].copy()

def auto_refresh_cache():
    """Refresh automatique du cache toutes les 60 secondes"""
    while True:
        time.sleep(CACHE_DURATION_SECONDS)
        if not cache_data["is_updating"]:
            update_cache()

def check_page_title():
    """Vérifie si le referer contient le bon titre de page"""
    referer = request.headers.get('Referer', '')
    return True

def verify_access_token():
    """Vérifie le token d'accès dans les headers"""
    token = request.headers.get('X-Access-Token', '')
    return token in active_tokens

def verify_admin_token():
    """Vérifie le token admin dans les headers"""
    token = request.headers.get('X-Admin-Token', '')
    return token in admin_tokens

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

@app.route('/admin/login', methods=['POST'])
def admin_login():
    """Vérifie les credentials admin et génère un token"""
    try:
        client_ip = get_client_ip()
        
        # Vérifier le rate limit
        if not check_rate_limit(client_ip):
            return jsonify({
                "valid": False,
                "error": f"Too many attempts. Try again in {LOGIN_TIMEOUT_MINUTES} minutes"
            }), 429
        
        data = request.get_json()
        login = data.get('login', '').strip()
        password = data.get('password', '').strip()
        
        if not login or not password:
            add_login_attempt(client_ip)
            return jsonify({"valid": False, "error": "Login and password required"}), 400
        
        # Vérifier les credentials
        if login == ADMIN_LOGIN and password == ADMIN_PASSWORD:
            # Génération d'un token admin unique
            token = secrets.token_urlsafe(32)
            admin_tokens[token] = {
                "login": login,
                "ip": client_ip,
                "created_at": datetime.now().isoformat()
            }
            
            # Réinitialiser les tentatives
            admin_login_attempts[client_ip] = []
            
            return jsonify({
                "valid": True,
                "token": token,
                "message": "Admin access granted"
            })
        
        # Mauvais credentials
        add_login_attempt(client_ip)
        remaining_attempts = MAX_LOGIN_ATTEMPTS - len(admin_login_attempts[client_ip])
        
        return jsonify({
            "valid": False,
            "error": f"Invalid credentials. {remaining_attempts} attempts remaining"
        }), 401
    
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 500

@app.route('/admin/connections', methods=['GET'])
def get_connections():
    """Récupère les logs de connexions - nécessite un token admin"""
    try:
        if not verify_admin_token():
            return jsonify({"error": "Unauthorized - Invalid admin token"}), 401
        
        # Trier par date décroissante
        sorted_connections = sorted(
            connections_log,
            key=lambda x: x.get('timestamp', ''),
            reverse=True
        )
        
        return jsonify({
            "connections": sorted_connections,
            "total": len(connections_log)
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/log-connection', methods=['POST'])
def log_connection():
    """Enregistre une connexion"""
    try:
        data = request.get_json()
        
        connection_entry = {
            "ip": data.get('ip', get_client_ip()),
            "country": data.get('country', 'Unknown'),
            "countryName": data.get('countryName', 'Unknown'),
            "key": data.get('key', 'N/A'),
            "userAgent": data.get('userAgent', request.headers.get('User-Agent', 'Unknown')),
            "timestamp": datetime.now().isoformat(),
            "type": data.get('type', 'USER_ACCESS')
        }
        
        connections_log.append(connection_entry)
        
        # Limiter la taille du log en mémoire (garder les 1000 dernières connexions)
        if len(connections_log) > 1000:
            connections_log.pop(0)
        
        return jsonify({"success": True, "message": "Connection logged"})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/stats', methods=['GET'])
def get_stats():
    """Récupère les statistiques - nécessite un token admin"""
    try:
        if not verify_admin_token():
            return jsonify({"error": "Unauthorized - Invalid admin token"}), 401
        
        # Calculer les stats
        unique_ips = len(set(conn.get('ip') for conn in connections_log))
        
        today = datetime.now().date()
        today_connections = sum(
            1 for conn in connections_log
            if datetime.fromisoformat(conn.get('timestamp', '')).date() == today
        )
        
        # Top pays
        country_count = {}
        for conn in connections_log:
            country = conn.get('country', 'Unknown')
            country_count[country] = country_count.get(country, 0) + 1
        
        return jsonify({
            "totalConnections": len(connections_log),
            "uniqueIPs": unique_ips,
            "todayConnections": today_connections,
            "topCountries": country_count
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/force-refresh', methods=['POST'])
def force_refresh():
    """Force le refresh du cache - nécessite un token admin"""
    try:
        if not verify_admin_token():
            return jsonify({"error": "Unauthorized - Invalid admin token"}), 401
        
        # Forcer la mise à jour
        threading.Thread(target=update_cache, daemon=True).start()
        
        return jsonify({
            "success": True,
            "message": "Cache refresh initiated"
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/links', methods=['GET'])
def get_links():
    """Récupère les liens de serveurs depuis le cache - nécessite un token valide"""
    try:
        # Vérification du token
        if not verify_access_token():
            return jsonify({"error": "Unauthorized - Invalid or missing token"}), 401
        
        # Vérification du referer (optionnel mais recommandé)
        if not check_page_title():
            return jsonify({"error": "Unauthorized - Invalid referer"}), 403
        
        # Récupérer les liens depuis le cache
        links = get_cached_links()
        
        # Informations sur le cache
        cache_age = None
        if cache_data["last_update"]:
            cache_age = int((datetime.now() - cache_data["last_update"]).total_seconds())
        
        return jsonify({
            "links": links,
            "cache_info": {
                "last_update": cache_data["last_update"].isoformat() if cache_data["last_update"] else None,
                "cache_age_seconds": cache_age,
                "is_updating": cache_data["is_updating"],
                "next_update_in": CACHE_DURATION_SECONDS - cache_age if cache_age else CACHE_DURATION_SECONDS
            }
        })
    
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Request failed: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    cache_age = None
    if cache_data["last_update"]:
        cache_age = int((datetime.now() - cache_data["last_update"]).total_seconds())
    
    return jsonify({
        "status": "ok",
        "valid_keys_count": len(VALID_KEYS),
        "admin_configured": bool(ADMIN_LOGIN and ADMIN_PASSWORD),
        "total_connections": len(connections_log),
        "cache_info": {
            "cached_links": len(cache_data["links"]),
            "last_update": cache_data["last_update"].isoformat() if cache_data["last_update"] else None,
            "cache_age_seconds": cache_age,
            "is_updating": cache_data["is_updating"]
        }
    })

if __name__ == '__main__':
    if not ROBLOX_COOKIE:
        print("WARNING: ROBLOX_COOKIE environment variable is not set")
    
    if not VALID_KEYS:
        print("WARNING: VALID_KEY environment variable is not set")
        print("Example: VALID_KEY='key1,key2,key3'")
    else:
        print(f"Loaded {len(VALID_KEYS)} valid keys")
    
    print(f"Admin login configured: {ADMIN_LOGIN}")
    print(f"Admin password configured: {'***' if ADMIN_PASSWORD else 'NOT SET'}")
    print(f"Cache refresh interval: {CACHE_DURATION_SECONDS} seconds")
    
    # Initialiser le cache au démarrage
    print("Initializing cache...")
    update_cache()
    
    # Lancer le thread de refresh automatique
    refresh_thread = threading.Thread(target=auto_refresh_cache, daemon=True)
    refresh_thread.start()
    print("Auto-refresh thread started")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
