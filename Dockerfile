# copia el código y el script
COPY . /app
WORKDIR /app

# Asegurar permisos ejecutables
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# CMD que ejecuta el script (shell script hará expandir $PORT)
CMD ["/app/start.sh"]
