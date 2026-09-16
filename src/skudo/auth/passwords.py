"""Hash de contraseñas con `scrypt` de la biblioteca estándar.

Por qué scrypt y no una dependencia nueva: está en `hashlib` desde Python 3.6,
lo provee OpenSSL, y es **memory-hard** — encarecer un ataque por GPU es
justamente lo que un hash rápido como SHA no hace. Agregar `argon2-cffi` sería
marginalmente mejor y trae una dependencia binaria a una plataforma que hoy no
tiene ninguna.

El formato guardado lleva sus PARÁMETROS adentro (`scrypt$n$r$p$sal$hash`). Sin
eso, subir el costo el año que viene invalidaría todas las contraseñas
existentes: con ellos, cada hash se verifica con los parámetros con los que se
creó y los nuevos nacen más caros.
"""

import hmac
import secrets
from hashlib import scrypt

# ~64 MB de memoria por verificación. Suficiente para que una GPU no sea barata
# y poco para un servidor que verifica un login de vez en cuando, no mil por
# segundo.
N = 2**16
R = 8
P = 1
SAL_BYTES = 16
CLAVE_BYTES = 32


def hash_password(password: str) -> str:
    """Devuelve `scrypt$n$r$p$sal$hash`, todo en hexadecimal."""
    if not password:
        raise ValueError("la contraseña no puede estar vacía")
    sal = secrets.token_bytes(SAL_BYTES)
    clave = scrypt(
        password.encode("utf-8"), salt=sal, n=N, r=R, p=P, dklen=CLAVE_BYTES,
        maxmem=N * R * 128 * 2,
    )
    return f"scrypt${N}${R}${P}${sal.hex()}${clave.hex()}"


def verify_password(password: str, guardado: str) -> bool:
    """Verifica con los parámetros CON LOS QUE SE CREÓ el hash, no con los de hoy.

    La comparación es en tiempo constante: `==` sobre bytes filtra, por el
    tiempo que tarda en cortar, cuántos caracteres del prefijo acertó quien
    prueba.
    """
    try:
        etiqueta, n, r, p, sal_hex, clave_hex = guardado.split("$")
        if etiqueta != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        sal = bytes.fromhex(sal_hex)
        esperado = bytes.fromhex(clave_hex)
    except (ValueError, AttributeError):
        return False
    intento = scrypt(
        password.encode("utf-8"), salt=sal, n=n, r=r, p=p, dklen=len(esperado),
        maxmem=n * r * 128 * 2,
    )
    return hmac.compare_digest(intento, esperado)


def generate_password(palabras: int = 24) -> str:
    """Una contraseña que un humano puede transcribir sin equivocarse.

    Se excluyen los caracteres ambiguos —0/O, 1/l/I— porque esta contraseña se
    va a dictar, copiar a mano y leer de una pantalla al menos una vez.
    """
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(palabras))
