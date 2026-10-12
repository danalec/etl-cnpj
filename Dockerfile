FROM python:3.10-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=1000:1000 . .
# O .dockerignore exclui data/ e logs/, entao eles nao vem no COPY. Sem criá-los
# aqui o `python -m src` quebra em PermissionError já no import de settings, que
# monta os diretorios no boot -- o Compose monta volumes por cima, mas um
# `docker run` direto nao tem essa sorte.
RUN mkdir -p /app/data /app/logs && chown -R 1000:1000 /app/data /app/logs
USER 1000
CMD ["python","-m","src"]
