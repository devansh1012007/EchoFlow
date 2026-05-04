import os, time, psycopg2

url = os.environ["DATABASE_URL"]
print("Waiting for database...")
while True:
    try:
        conn = psycopg2.connect(url)
        conn.close()
        print("Database ready.")
        break
    except psycopg2.OperationalError:
        time.sleep(1)