FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple/
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt
COPY suite ./suite
COPY static ./static
COPY samples ./samples
COPY serve.py seed_demo.py ./
ENV APP_MODE=demo APP_DATA=/data
VOLUME ["/data"]
EXPOSE 8002
CMD ["python", "serve.py", "--host", "0.0.0.0", "--port", "8002"]
