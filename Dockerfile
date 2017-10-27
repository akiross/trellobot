FROM python:3.6

# Move app
WORKDIR /app
ADD . /app

# Install deps
RUN pip3 install -r requirements.txt

# Run bot
CMD ["python3", "main.py"]

