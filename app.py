from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import uuid

app = Flask(__name__)
app.secret_key = 'super_secret_key'

# Tells SQLite to run entirely in the server RAM to bypass all disk restrictions
DB_PATH = ':memory:'

# Global connection placeholder for memory persistence
_db_conn = None

def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return _db_conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS polls (
            poll_id TEXT PRIMARY KEY,
            question TEXT,
            feedback_question TEXT,
            host_secret TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id TEXT,
            option_text TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id TEXT,
            student_name TEXT,
            candidate_chosen TEXT,
            feedback_answer TEXT,
            UNIQUE(poll_id, student_name)
        )
    ''')
    conn.commit()

@app.route('/')
def index():
    return render_template('create_poll.html')

@app.route('/create', methods=['POST'])
def create_poll():
    question = request.form.get('question')
    feedback_question = request.form.get('feedback_question', '').strip()
    options = [opt.strip() for opt in request.form.getlist('options') if opt.strip()]
    
    if not question or len(options) < 2:
        return "Please provide a question and at least 2 choices!", 400

    poll_id = str(uuid.uuid4())[:8]
    host_secret = str(uuid.uuid4())[:12]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO polls (poll_id, question, feedback_question, host_secret) VALUES (?, ?, ?, ?)", 
                   (poll_id, question, feedback_question, host_secret))
    for option in options:
        cursor.execute("INSERT INTO options (poll_id, option_text) VALUES (?, ?)", (poll_id, option))
    conn.commit()

    return render_template('poll_created.html', poll_id=poll_id, host_secret=host_secret, base_url=request.host_url)

@app.route('/poll/<poll_id>')
def view_poll(poll_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT question, feedback_question FROM polls WHERE poll_id = ?", (poll_id,))
    poll = cursor.fetchone()
    if not poll:
        return "Poll not found!", 404
    
    cursor.execute("SELECT option_text FROM options WHERE poll_id = ?", (poll_id,))
    options = [row[0] for row in cursor.fetchall()]
    
    return render_template('vote.html', poll_id=poll_id, question=poll[0], feedback_question=poll[1], options=options)

@app.route('/poll/<poll_id>/vote', methods=['POST'])
def submit_vote(poll_id):
    name = request.form.get('student_name').strip().lower()
    choice = request.form.get('choice')
    feedback_answer = request.form.get('feedback_answer', '').strip()
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO votes (poll_id, student_name, candidate_chosen, feedback_answer) VALUES (?, ?, ?, ?)", 
                       (poll_id, name, choice, feedback_answer))
        conn.commit()
        return "<h3>Vote and feedback submitted successfully!</h3>"
    except sqlite3.IntegrityError:
        return "<h3>Error: You have already voted in this poll!</h3>", 400

@app.route('/dashboard/<host_secret>')
def view_dashboard(host_secret):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT poll_id, question, feedback_question FROM polls WHERE host_secret = ?", (host_secret,))
    poll = cursor.fetchone()
    if not poll:
        return "Invalid secret dashboard key!", 404
    
    poll_id, question, feedback_question = poll[0], poll[1], poll[2]
    
    cursor.execute("SELECT candidate_chosen, COUNT(*) FROM votes WHERE poll_id = ? GROUP BY candidate_chosen", (poll_id,))
    summary = cursor.fetchall()
    
    cursor.execute("SELECT student_name, candidate_chosen, feedback_answer FROM votes WHERE poll_id = ?", (poll_id,))
    detailed_votes = cursor.fetchall()
    
    return render_template('dashboard.html', question=question, feedback_question=feedback_question, summary=summary, detailed_votes=detailed_votes)

# Initialize database tables inside the memory instance immediately upon loading
init_db()

if __name__ == '__main__':
    app.run(debug=True)
