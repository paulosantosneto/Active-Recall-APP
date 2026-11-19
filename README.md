# Active Recall App

## Requirements

- Python 3.11
- pip

## Installation

Clone the repository:

```bash
git clone https://github.com/paulosantosneto/Active-Recall-APP.git
cd Active-Recall-APP
```

Create and activate a virtual environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=your_api_key_here
```

Replace `your_api_key_here` with your OpenAI API key.

**Important:** Never commit your `.env` file or expose your API key.

## Running the Application

Start the development server:

```bash
uvicorn app.main:app --reload
```

Access the application:

- API: http://127.0.0.1:8000
- Interactive API documentation: http://127.0.0.1:8000/docs

Press `Ctrl + C` to stop the server.

## License

License not yet specified.
