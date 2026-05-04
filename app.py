from flask import Flask, render_template, request, Response
from agent import agent

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message")

    def generate():
        response = agent(user_input)

        # preserve formatting properly
        for chunk in response.splitlines(keepends=True):
            yield chunk

    return Response(generate(), mimetype='text/plain')


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
