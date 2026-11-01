# Apify Python base image. Not built or tested in this repo; check the tag before publishing.
FROM apify/actor-python:3.13

WORKDIR /usr/src/app

# The only runtime dependency is the Apify SDK; everything else is the standard library.
RUN pip install --no-cache-dir apify==4.2.0

COPY src ./src

CMD ["python3", "src/actor.py"]
