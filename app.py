from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
import sqlite3
import uuid
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'super_secret_dev_key_change_me')

# Setup Authentication Extensions
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID', 'placeholder_id'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', 'placeholder_secret'),
    access_token_url='https://accounts.google.com/o/oauth2/token',
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
    client_kwargs={'scope': 'openid email profile'},
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration'
)

DB_PATH = ':memory:'
_db_conn = None

def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return _db_conn

class User(UserMixin):
    def __init__(self, id, email, name):
        self.id = id
        self.email = email
        self.name = name

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, name FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        return User(row[0], row[1], row[2])
    return None

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE,
            name TEXT,
            password_hash TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS polls (
            poll_id TEXT PRIMARY KEY,
            creator_id TEXT,
            question TEXT,
            feedback_question TEXT,
            host_secret TEXT,
            expires_at TEXT,
            FOREIGN KEY(creator_id) REFERENCES users(id)
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
            roll_number TEXT,
            candidate_chosen TEXT,
            feedback_answer TEXT,
            UNIQUE(poll_id, roll_number)
        )
    ''')
    conn.commit()

@app.route('/login')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login/google')
def login_google():
    redirect_uri = url_for('google_authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_authorize():
    token = google.authorize_access_token()
    resp = google.get('userinfo')
    user_info = resp.json()
    email = user_info['email']
    name = user_info.get('name', email.split('@')[0])
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    
    if row:
        user_id = row[0]
    else:
        user_id = str(uuid.uuid4())[:8]
        cursor.execute("INSERT INTO users (id, email, name) VALUES (?, ?, ?)", (user_id, email, name))
        conn.commit()
        
    user = User(user_id, email, name)
    login_user(user)
    return redirect(url_for('dashboard'))

@app.route('/login/email', methods=['POST'])
def login_email():
    email = request.form.get('email').strip().lower()
    name = email.split('@')[0]
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    
    if row:
        user_id, user_name = row[0], row[1]
    else:
        user_id = str(uuid.uuid4())[:8]
        user_name = name
        cursor.execute("INSERT INTO users (id, email, name) VALUES (?, ?, ?)", (user_id, email, user_name))
        conn.commit()
        
    user = User(user_id, email, user_name)
    login_user(user)
    return redirect(url_for('dashboard'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login_page'))

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login_page'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT poll_id, question, expires_at, host_secret FROM polls WHERE creator_id = ?", (current_user.id,))
    user_polls = cursor.fetchall()
    
    polls_list = []
    for row in user_polls:
        poll_id, question, expires_at, host_secret = row
        cursor.execute("SELECT COUNT(*) FROM votes WHERE poll_id = ?", (poll_id,))
        vote_count = cursor.fetchone()[0]
        expiry = datetime.fromisoformat(expires_at)
        is_active = datetime.utcnow() < expiry
        
        polls_list.append({
            'id': poll_id,
            'question': question,
            'vote_count': vote_count,
            'is_active': is_active,
            'host_secret': host_secret
        })
        
    return render_template('dashboard.html', polls=polls_list)

@app.route('/poll/new')
@login_required
def new_poll_page():
    return render_template('create_poll.html')

@app.route('/create', methods=['POST'])
@login_required
def create_poll():
    question = request.form.get('question')
    feedback_question = request.form.get('feedback_question', '').strip()
    options = [opt.strip() for opt in request.form.getlist('options') if opt.strip()]
    duration_hours = request.form.get('duration', type=float, default=24.0)
    
    if not question or len(options) < 2:
        return "Please specify a query and at least 2 target variables.", 400

    poll_id = str(uuid.uuid4())[:8]
    host_secret = str(uuid.uuid4())[:12]
    expires_at = (datetime.utcnow() + timedelta(hours=duration_hours)).isoformat()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO polls (poll_id, creator_id, question, feedback_question, host_secret, expires_at) VALUES (?, ?, ?, ?, ?, ?)", 
                   (poll_id, current_user.id, question, feedback_question, host_secret, expires_at))
    for option in options:
        cursor.execute("INSERT INTO options (poll_id, option_text) VALUES (?, ?)", (poll_id, option))
    conn.commit()

    return redirect(url_for('poll_links_page', poll_id=poll_id))

@app.route('/poll/links/<poll_id>')
@login_required
def poll_links_page(poll_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT question, host_secret FROM polls WHERE poll_id = ? AND creator_id = ?", (poll_id, current_user.id))
    poll = cursor.fetchone()
    if not poll:
        return "Unauthorized profile mismatch error.", 403
    return render_template('poll_created.html', poll_id=poll_id, host_secret=poll[1], base_url=request.host_url, question=poll[0])

@app.route('/poll/<poll_id>')
def view_poll(poll_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT question, feedback_question, expires_at FROM polls WHERE poll_id = ?", (poll_id,))
    poll = cursor.fetchone()
    if not poll:
        return "Target structure context string missing.", 404
    
    expiry_time = datetime.fromisoformat(poll[2])
    is_expired = datetime.utcnow() > expiry_time

    cursor.execute("SELECT option_text FROM options WHERE poll_id = ?", (poll_id,))
    options = [row[0] for row in cursor.fetchall()]
    
    return render_template('vote.html', poll_id=poll_id, question=poll[0], feedback_question=poll[1], expires_at=poll[2], is_expired=is_expired, options=options)

@app.route('/poll/<poll_id>/vote', methods=['POST'])
def submit_vote(poll_id):
    name = request.form.get('student_name').strip()
    roll_number = request.form.get('roll_number').strip().upper()
    choice = request.form.get('choice')
    feedback_answer = request.form.get('feedback_answer', '').strip()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT expires_at FROM polls WHERE poll_id = ?", (poll_id,))
    poll = cursor.fetchone()
    if poll and datetime.utcnow() > datetime.fromisoformat(poll[0]):
        return "<h3>Error: This poll has already closed!</h3>", 400
    
    try:
        cursor.execute("INSERT INTO votes (poll_id, student_name, roll_number, candidate_chosen, feedback_answer) VALUES (?, ?, ?, ?, ?)", 
                       (poll_id, name, roll_number, choice, feedback_answer))
        conn.commit()
        return "<h3>Vote and feedback submitted successfully!</h3>"
    except sqlite3.IntegrityError:
        return "<h3>Error: A vote has already been submitted with this Roll Number!</h3>", 400

@app.route('/analytics/<host_secret>')
@login_required
def view_analytics(host_secret):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT poll_id, question, feedback_question FROM polls WHERE host_secret = ? AND creator_id = ?", (host_secret, current_user.id))
    poll = cursor.fetchone()
    if not poll:
        return "Access token permission error.", 403
    
    poll_id, question, feedback_question = poll[0], poll[1], poll[2]
    cursor.execute("SELECT candidate_chosen, COUNT(*) FROM votes WHERE poll_id = ? GROUP BY candidate_chosen", (poll_id,))
    summary = cursor.fetchall()
    cursor.execute("SELECT student_name, roll_number, candidate_chosen, feedback_answer FROM votes WHERE poll_id = ?", (poll_id,))
    detailed_votes = cursor.fetchall()
    
    return render_template('analytics.html', question=question, feedback_question=feedback_question, summary=summary, detailed_votes=detailed_votes)

init_db()

if __name__ == '__main__':
    app.run(debug=True)
