from flask import Flask, request

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return {"message": "Hello from Flask on Vercel!"}

# Vercel expects a variable named 'app'
# No need to run app.run()
