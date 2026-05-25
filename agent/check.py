import sqlite3, os
db_dir = 'databases'
for db in os.listdir(db_dir):
    if db == 'master_database.db':
        continue
print(f'\n=== {db} ===')
conn = sqlite3.connect(f'{db_dir}/{db}')
for table in ['file_logs','network_logs','process_logs','auth_logs','system_logs']:
    try:
        count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        print(f'  {table}: {count} rows')
    except:
        print(f'  {table}: not created yet')
conn.close()