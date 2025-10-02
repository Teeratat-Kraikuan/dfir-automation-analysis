#!/bin/sh

while ! ./manage.py sqlflush > /dev/null 2>&1 ;do
	echo "Waiting for the db to be ready."
	sleep 1
done

if [ -d "/home/django" ]
then
	python ./manage.py makemigrations
	python ./manage.py migrate
	python ./manage.py collectstatic --no-input
	python ./manage.py runserver 0.0.0.0:8000
	# reloadium run manage.py runserver 0.0.0.0:8001 --noreload
fi

exec "$@"