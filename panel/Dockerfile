# Imagen del panel: CLI de Docker (para poder manejar el contenedor
# shellngn-pro desde adentro, hablando con el socket del host) + Python +
# bash + cron. No copiamos panel/ ni scripts/ acá adentro -- el
# docker-compose.yml monta el repo completo del host en el mismo path
# dentro del contenedor, así un cambio en el código se ve solo con
# reiniciar el contenedor, sin reconstruir la imagen.
FROM docker:27-cli

RUN apk add --no-cache python3 bash curl

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
