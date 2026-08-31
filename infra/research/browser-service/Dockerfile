FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /service
RUN pip install --no-cache-dir fastapi==0.115.5 uvicorn==0.32.1 pydantic==2.10.3
COPY app.py /service/app.py

RUN useradd --create-home --uid 10001 browserworker && chown -R browserworker:browserworker /service
USER browserworker

EXPOSE 8010
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]

