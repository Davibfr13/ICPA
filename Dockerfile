FROM python:3.11-slim

WORKDIR /app

# Instalar Node.js e dependências do sistema
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependências do Flask
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copiar toda a aplicação
COPY . .

# Instalar Evolution API (versão anterior)
WORKDIR /app/evolution
RUN npm install

# Voltar para a pasta principal
WORKDIR /app

# Criar diretórios usados pelo Flask
RUN mkdir -p uploads/thumbs

# Expor as duas portas (Evolution 8080, Flask 5000)
EXPOSE 5000 8080

# Rodar ambos os servidores em paralelo
CMD ["bash", "-c", "cd evolution && npm start & gunicorn --bind 0.0.0.0:5000 app:app"]
