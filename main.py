import sqlite3
import subprocess
import os
import json

database = 'mmclisms.db'

def query_db(query, args=(), one=False):
    """Query the database and return the results. Commits for non-SELECT queries."""
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(query, args)
    qtype = query.strip().split()[0].upper() if query.strip() else ''
    if qtype == 'SELECT':
        rv = cur.fetchall()
        conn.close()
        return (rv[0] if rv else None) if one else rv
    else:
        conn.commit()
        lastrowid = cur.lastrowid
        conn.close()
        return lastrowid

def init_db():
    """Initialize the database with the schema."""
    # Check if database file exists
    db_exists = os.path.exists(database)
    # Query database and check if history table exists
    if db_exists:
        table = query_db("SELECT name FROM sqlite_master WHERE type='table' AND name='history';", one=True)
        if table:
            return  # Table exists, no need to initialize
        else:
            print("Database file found but history table is missing. Creating table...")
            response = input("Do you want to create the history table? (y/n): ")
            if response.lower() != 'y':
                print("Exiting without creating table.")
                return
            # Create the history table (SQLite compatible)
            query_db("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, tel VARCHAR(30) NOT NULL, last_message TIMESTAMP NOT NULL);")
            print("History table created.")
    else:
        # If db does not exist, create it and the table
        print("Database file not found. Creating database and history table: " + database)
        # create file by creating table
        query_db("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, tel VARCHAR(30) NOT NULL, last_message TIMESTAMP NOT NULL);")
        print("Database and history table created.")
    pass

def get_history():
    """Retrieve all history records."""
    return query_db("SELECT * FROM history ORDER BY last_message DESC;")

def add_history(tel, timestamp):
    """Add a new history record."""
    query_db("INSERT INTO history (tel, last_message) VALUES (?, ?);", (tel, timestamp))

def get_modem_id():
    try:
        res = subprocess.run(['mmcli', '-L', '--output-json'], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        paths = data.get('modem-list') or []
        if not paths:
            print("No modems found.")
            return None
        modem_id = paths[0].rstrip('/').split('/')[-1]
        return modem_id
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"Error executing mmcli: {e}")
        return None

def get_modem_info(modem_id):
    """Return (tel, enabled) for the given modem id, or None on error."""
    try:
        res = subprocess.run(['mmcli', '-m', str(modem_id), '--output-json'], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        modem = data.get('modem', {})
        generic = modem.get('generic', {})
        own_numbers = generic.get('own-numbers') or []
        tel = own_numbers[0] if own_numbers else None
        state = generic.get('state')
        enabled = (state == 'enabled')
        return tel, enabled
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"Error reading modem {modem_id}: {e}")
        return None

def set_modem_enabled(modem_id, enable=True):
    """Enable or disable the modem."""
    try:
        cmd = ['mmcli', '-m', str(modem_id), '--enable'] if enable else ['mmcli', '-m', str(modem_id), '--disable']
        subprocess.run(cmd, check=True)
        print("Modem enabled." if enable else "Modem disabled.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error changing modem state: {e}")
        return False

def send_sms(modem_id, tel, message):
    """Send an SMS using the specified modem.

    Uses mmcli --messaging-create-sms="text='...',number='...'" and parses the returned SMS DBus path,
    then invokes mmcli -s <path> --send. Records number in history on success.
    """
    import re
    from datetime import datetime

    try:
        payload = f"text='{message}',number='{tel}'"
        res = subprocess.run(
            ['mmcli', '-m', str(modem_id), f'--messaging-create-sms={payload}'],
            capture_output=True, text=True, check=True
        )
        out = res.stdout or res.stderr or ''
        # mmcli prints the created SMS path like: /org/freedesktop/ModemManager1/SMS/12
        m = re.search(r"(/org/freedesktop/ModemManager1/SMS/\d+)", out)
        if not m:
            print("Failed to create SMS (no SMS path found).")
            print(out.strip())
            return False
        sms_path = m.group(1)

        # send the created SMS
        subprocess.run(['mmcli', '-s', sms_path, '--send'], check=True)
        print("SMS sent successfully.")
        add_history(tel, datetime.now().isoformat())
        return True
    except subprocess.CalledProcessError as e:
        print(f"mmcli error: {e}")
        if e.stdout:
            print(e.stdout)
        if e.stderr:
            print(e.stderr)
        return False
    except Exception as e:
        print(f"Unexpected error sending SMS: {e}")
        return False

def check_received_sms(modem_id):
    """List received SMS messages for the modem and print basic info."""
    try:
        res = subprocess.run(['mmcli', '-m', str(modem_id), '--messaging-list-sms', '--output-json'], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        sms_paths = []

        # recursively collect SMS paths
        # TODO: improve this
        def collect(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    collect(v)
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, str) and item.startswith('/org/freedesktop/ModemManager1/SMS/'):
                        sms_paths.append(item)
                    else:
                        collect(item)
        collect(data)

        if not sms_paths:
            print("No SMS messages found.")
            return []

        messages = []
        for p in sms_paths:
            try:
                r = subprocess.run(['mmcli', '-s', p, '--output-json'], capture_output=True, text=True, check=True)
                d = json.loads(r.stdout)
                sms = d.get('sms') or d.get('message') or d
                # try to extract common fields
                number = sms.get('number') or sms.get('from') or sms.get('sender') or sms.get('tel') or None
                text = sms.get('text') or sms.get('content') or sms.get('payload') or None
                timestamp = sms.get('timestamp') or sms.get('date') or None
                state = sms.get('state') or None
                messages.append({'path': p, 'number': number, 'text': text, 'timestamp': timestamp, 'state': state, 'raw': sms})
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                print(f"Failed to read SMS {p}, printing raw output.")
                print(r.stdout if 'r' in locals() else '')
        # Print summary
        for i, m in enumerate(messages, start=1):
            print(f"{i}) From: {m['number'] or 'unknown'}  Time: {m['timestamp'] or 'unknown'}  State: {m['state'] or 'unknown'}")
            print(f"   Text: {m['text'] or '[no text parsed]'}")
        return messages
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"Error listing SMS: {e}")
        return []

def choose_tel_from_history():
    history = get_history()
    if not history:
        print("No history entries.")
        return None
    for i, row in enumerate(history, start=1):
        print(f"{i}) {row['tel']}  last: {row['last_message']}")
    try:
        idx = int(input("Select number index: ").strip())
        if 1 <= idx <= len(history):
            return history[idx-1]['tel']
    except ValueError:
        pass
    print("Invalid selection.")
    return None

def prompt_send_sms(modem_id):
    choice = input("Send SMS - choose: [h]istory / [t]ype: ").strip().lower()
    if choice == 'h':
        tel = choose_tel_from_history()
        if not tel:
            return
    else:
        tel = input("Enter telephone number (with country code, e.g. +48123456789): ").strip()
        if not tel:
            print("No number provided.")
            return
    message = input("Enter message text: ").strip()
    if not message:
        print("No message provided.")
        return
    send_sms(modem_id, tel, message)

def clear_screen():
    """Clear terminal screen (Linux)."""
    os.system('clear')

def interactive_menu():
    modem_id = get_modem_id()
    if modem_id is None:
        print("No modem available. Exiting.")
        return
    while True:
        clear_screen()
        info = get_modem_info(modem_id)
        tel, enabled = (None, False)
        if info:
            tel, enabled = info
        print("\n--- MMCli SMS CLIENT ---")
        print("Made by Kerso 2025")
        print(f"Modem ID: {modem_id}")
        print("1) Enable modem")
        print("2) Disable modem")
        print("3) Display telephone number")
        print("4) Send SMS")
        print("5) Check received SMS")
        print("6) Exit")
        choice = input("Select action (1-6): ").strip()
        if choice == '1':
            set_modem_enabled(modem_id, True)
        elif choice == '2':
            set_modem_enabled(modem_id, False)
        elif choice == '3':
            if tel:
                print("Telephone number:", tel)
            else:
                print("Telephone number not available.")
        elif choice == '4':
            prompt_send_sms(modem_id)
        elif choice == '5':
            check_received_sms(modem_id)
        elif choice == '6':
            break
        else:
            print("Invalid choice, try again.")
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    init_db()
    interactive_menu()
