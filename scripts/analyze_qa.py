import sqlite3
from datetime import datetime
import json
import urllib.request

def analyze():
    db_path = "ecs-data/agent_jobs.db"
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return

    # 1. QA Visitor Logs
    cursor.execute("SELECT * FROM qa_visitors ORDER BY visited_at DESC")
    visitors = cursor.fetchall()
    
    print("=== QA Visitor Logs ===")
    print(f"Total QA visits: {len(visitors)}")
    
    # Group by IP
    ip_counts = {}
    ip_times = {}
    for v in visitors:
        ip = v['ip_address']
        ip_counts[ip] = ip_counts.get(ip, 0) + 1
        if ip not in ip_times:
            ip_times[ip] = []
        ip_times[ip].append(v['visited_at'])
        
    print("\nVisits by IP Address:")
    for ip, count in sorted(ip_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"- {ip}: {count} visits")

    # 2. Try to Geolocate IP addresses using a free public IP API (ip-api.com)
    print("\n=== Geolocation Conclusion ===")
    for ip in ip_counts:
        if ip in ("127.0.0.1", "localhost", "unknown"):
            print(f"- {ip}: Localhost / Private network")
            continue
        try:
            # We fetch geolocation info
            with urllib.request.urlopen(f"http://ip-api.com/json/{ip}", timeout=3) as response:
                data = json.loads(response.read().decode())
                if data.get("status") == "success":
                    country = data.get("country", "Unknown")
                    region = data.get("regionName", "Unknown")
                    city = data.get("city", "Unknown")
                    org = data.get("org", "Unknown")
                    print(f"- {ip}: {country} ({region}, {city}) - {org}")
                else:
                    print(f"- {ip}: Failed to resolve location info")
        except Exception as e:
            print(f"- {ip}: Error geolocating ({e})")
            
    conn.close()

if __name__ == "__main__":
    analyze()
