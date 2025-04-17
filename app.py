import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib
import requests
from flask import Flask, request, render_template, send_file, abort, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "https://jmerle.github.io"}})

app.config['UPLOAD_FOLDER'] = "/tmp/uploads"
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
shutil.copy2("uploads/datamodel.py", os.path.join(app.config["UPLOAD_FOLDER"], "datamodel.py"))
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key')  # Change in production
PASSWORD = os.environ.get('PASSWORD')


@app.route('/', methods=['GET', 'POST'])
def upload_file():
    # If it's a POST request (file upload)
    if request.method == 'POST':
        # Check if the post request has the file part
        if 'file' not in request.files:
            return 'No file part'
        file = request.files['file']
        round = request.form['round']

        if file.filename == '':
            return 'No selected file'

        if file:
            file_data = file.read()
            filename = hashlib.blake2b(file_data).hexdigest()

            # Save to permanent location
            saved_path = os.path.join(app.config['UPLOAD_FOLDER'], filename + ".py")
            with open(saved_path, "wb") as f:
                f.write(file_data)

            # Now run prosperity3bt
            log_path = saved_path + ".log"
            command = ['prosperity3bt', saved_path, round, '--out', log_path]
            if round == "all":
                command = ['prosperity3bt', saved_path, '--out', log_path]
            if len(round.split()) > 1:
                command = ["prosperity3bt", saved_path] + round.split() + ['--out', log_path]

            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")

            encoded_file_path = urllib.parse.quote(f"https://imcprosperityrunner-production.up.railway.app/{filename}.py.log")
            prosperity_url = f"https://jmerle.github.io/imc-prosperity-3-visualizer/?open={encoded_file_path}"

            show_tip1 = False
            if "ValueError: invalid literal for int() with base 10: ''" in result.stderr:
                show_tip1 = True

            uploads = sorted(
                (os.path.join(app.config['UPLOAD_FOLDER'], f) for f in os.listdir(app.config['UPLOAD_FOLDER'])),
                key=os.path.getmtime,
                reverse=True
            )

            for old_file in uploads[5:]:
                try:
                    os.remove(old_file)
                except Exception as e:
                    print(f"Failed to delete {old_file}: {e}")

            return render_template('upload.html', success=True, data=result.stdout, errors=result.stderr,
                                   prosperity_url=prosperity_url, show_tip1=show_tip1)

    return render_template('upload.html')

@app.route('/<path:filename>')
def serve_file(filename):
    if '..' in filename or filename.startswith('/'):
        abort(400)
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except FileNotFoundError:
        abort(404)

@app.route('/list', methods=['GET', 'POST'])
def list_files():
    if session.get('authenticated') != True:
        if request.method == 'POST':
            if request.form.get('password') == PASSWORD:
                session['authenticated'] = True
                return redirect(url_for('list_files'))
            else:
                return render_template('login.html', error="Wrong password")
        return render_template('login.html')

    files = sorted(os.listdir(app.config['UPLOAD_FOLDER']))
    return render_template('list.html', files=files)


if __name__ == '__main__':
    app.run(debug=True)
