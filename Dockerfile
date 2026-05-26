FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
# 容器内绑定 0.0.0.0，通过环境变量控制
ENV KB_HOST=0.0.0.0
CMD ["python", "backend/server.py"]
