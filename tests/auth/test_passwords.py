import pytest

from skudo.auth.passwords import generate_password, hash_password, verify_password


def test_una_contrasena_correcta_verifica():
    h = hash_password("correcto caballo batería grapa")
    assert verify_password("correcto caballo batería grapa", h) is True


def test_una_contrasena_incorrecta_no_verifica():
    h = hash_password("la buena")
    assert verify_password("la mala", h) is False
    assert verify_password("", h) is False


def test_el_mismo_texto_produce_hashes_distintos():
    """Sal aleatoria: dos usuarios con la misma contraseña no comparten hash, y
    una tabla robada no delata quiénes eligieron lo mismo."""
    assert hash_password("igual") != hash_password("igual")


def test_el_hash_lleva_sus_parametros_adentro():
    """Es lo que permite subir el costo el año que viene sin invalidar las
    contraseñas ya guardadas."""
    h = hash_password("x")
    etiqueta, n, r, p, sal, _clave = h.split("$")
    assert etiqueta == "scrypt"
    assert int(n) >= 2**15 and int(r) >= 8 and int(p) >= 1
    assert len(bytes.fromhex(sal)) == 16


def test_un_hash_con_parametros_viejos_se_sigue_verificando():
    """Se fabrica a mano un hash con un costo menor y tiene que verificar: si el
    verificador usara los parámetros de HOY en vez de los del hash, todos los
    usuarios quedarían afuera el día que se suba el costo."""
    from hashlib import scrypt

    sal = bytes(16)
    clave = scrypt(b"vieja", salt=sal, n=2**14, r=8, p=1, dklen=32)
    guardado = f"scrypt${2**14}$8$1${sal.hex()}${clave.hex()}"
    assert verify_password("vieja", guardado) is True


def test_un_hash_corrupto_no_explota_dice_que_no():
    for basura in ["", "nada", "scrypt$x$y$z$a$b", "bcrypt$1$2$3$aa$bb", None]:
        assert verify_password("x", basura) is False


def test_una_contrasena_vacia_no_se_puede_guardar():
    with pytest.raises(ValueError):
        hash_password("")


def test_la_contrasena_generada_evita_caracteres_ambiguos():
    """Se va a dictar y a transcribir a mano al menos una vez."""
    for _ in range(20):
        p = generate_password()
        assert len(p) == 24
        assert not (set(p) & set("0O1lI"))
