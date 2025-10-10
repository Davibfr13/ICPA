FROM python:3.11-slim

WORKDIR /app

# Instalar dependências do sistema e Node.js
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante do projeto
COPY . .

# Instalar dependências da Evolution API
WORKDIR /app/evolution
RUN npm install

# Voltar ao diretório do Flask
WORKDIR /app

# Criar diretórios de upload
RUN mkdir -p uploads/thumbs

EXPOSE 5000 8080

# Iniciar Evolution + Flask simultaneamente
CMD bash -c "cd evolution && npm start & gunicorn --bind 0.0.0.0:5000 app:app"
