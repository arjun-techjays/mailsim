# Allowlist egress proxy (stdlib only). See proxy.py and docs/egress.md.
FROM python:3.12-slim
RUN useradd --system --no-create-home egress
COPY proxy.py /opt/egress/proxy.py
USER egress
EXPOSE 3128
ENTRYPOINT ["python", "-I", "/opt/egress/proxy.py"]
