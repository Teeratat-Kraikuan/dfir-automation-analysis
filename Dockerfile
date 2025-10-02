FROM python:3-bookworm

ENV PYTHONUNBUFFERED=1

RUN apt update -y && apt upgrade -y \
 && apt-get install -y --no-install-recommends docker.io \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip
COPY ./requirements.txt .
RUN pip install -r requirements.txt

COPY ./django /home/django/

WORKDIR /home/django/

COPY ./entrypoint.sh /usr/local/bin/entrypoint.sh 
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]
# CMD [ "python", "manage.py", "runserver", "0.0.0.0:8000" ]