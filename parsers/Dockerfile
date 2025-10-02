# Dockerfile.parsers
FROM mcr.microsoft.com/dotnet/runtime:9.0

WORKDIR /opt/tools
COPY tools/ /opt/tools/

WORKDIR /usr/local/bin
COPY parse.sh /usr/local/bin/parse
RUN chmod +x /usr/local/bin/parse

RUN useradd -m appuser
USER appuser

WORKDIR /work
ENTRYPOINT ["parse"]