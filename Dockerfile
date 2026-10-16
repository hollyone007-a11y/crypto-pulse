FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg
COPY requirements-live.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-live.txt
COPY cryptopulse ./cryptopulse
# состояние (SQLite, журнал, кэш свечей) — в томе /data
WORKDIR /data
ENV PYTHONPATH=/app
HEALTHCHECK --interval=5m --timeout=10s CMD python -c "import sqlite3,os,time,sys; p='/data/cryptopulse.db'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 7200 else 1)"
CMD ["python", "-m", "cryptopulse.cli", "telegram", "--mode", "paper"]
