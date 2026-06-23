FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN apk add --no-cache \
        git \
        gcc \
        musl-dev \
        curl \
        libffi-dev \
        ca-certificates

# Corporate certs — TLS towards Azure DevOps and callback endpoints
COPY certs/localCA.crt /usr/local/share/ca-certificates/localCA.crt
COPY certs/zurichseguros-rootca-until-2031_03_20.crt /usr/local/share/ca-certificates/zurichseguros-rootca.crt
COPY certs/cacert-workflow-uat.pem /usr/local/share/ca-certificates/cacert-workflow-uat.crt
RUN update-ca-certificates

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "app.py"]
