ICPA-AGENDADOR - Configuração para Render

Passos para deploy manual no Render:
1. Suba este repositório no GitHub.
2. No Render, crie um novo Web Service e conecte ao repositório.
3. Em 'Environment' adicione as variáveis de ambiente (ou use the managed Postgres).
4. Crie o Managed Postgres (Render) e copie DATABASE_URL para o serviço web.
5. Defina EVOLUTION_API_URL apontando para sua Evolution API externa.
