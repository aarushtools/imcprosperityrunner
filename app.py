import hashlib
import os
import subprocess
import tempfile
import urllib
import requests
from flask import Flask, request, render_template, send_file, abort, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "https://jmerle.github.io"}})

app.config['UPLOAD_FOLDER'] = "uploads"


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

            encoded_file_path = urllib.parse.quote(f"http://localhost:5000/{filename}.py.log")
            prosperity_url = f"https://jmerle.github.io/imc-prosperity-3-visualizer/?open={encoded_file_path}"

            show_tip1 = False
            if "ValueError: invalid literal for int() with base 10: ''" in result.stderr:
                show_tip1 = True

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

if __name__ == '__main__':
    app.run(debug=True)
