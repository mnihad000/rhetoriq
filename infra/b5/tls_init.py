"""Container-only local CA initialization. Private keys never mount into API."""
from pathlib import Path
import os
import subprocess


def run(*args):
    subprocess.run(["openssl",*args],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    private=Path("/ca-private")
    public=Path("/ca-public")
    private.mkdir(parents=True,exist_ok=True)
    public.mkdir(parents=True,exist_ok=True)
    ca_key=private/"ca.key"
    ca_cert=public/"ca.crt"
    if not ca_cert.exists():
        run("req","-x509","-newkey","rsa:3072","-nodes","-sha256","-days","3650",
            "-subj","/CN=RhetoriQ local development CA","-keyout",str(ca_key),"-out",str(ca_cert))
        ca_key.chmod(0o600)
        ca_cert.chmod(0o644)
    if not ca_key.exists():
        raise RuntimeError("Existing CA certificate has no private key; refusing an inconsistent CA")
    for name,uid in (("elasticsearch",1000),("neo4j",7474),("redis",999),("postgres",999)):
        directory=Path("/certificates")/name
        directory.mkdir(parents=True,exist_ok=True)
        key=directory/"private.key"
        certificate=directory/"public.crt"
        if not certificate.exists():
            request=private/f"{name}.csr"
            extensions=private/f"{name}.ext"
            extensions.write_text(f"subjectAltName=DNS:{name},DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth,clientAuth\n",encoding="ascii")
            run("req","-new","-newkey","rsa:2048","-nodes","-subj",f"/CN={name}","-keyout",str(key),"-out",str(request))
            run("x509","-req","-in",str(request),"-CA",str(ca_cert),"-CAkey",str(ca_key),"-CAcreateserial",
                "-CAserial",str(private/"ca.srl"),"-out",str(certificate),"-days","825","-sha256","-extfile",str(extensions))
        run("verify","-CAfile",str(ca_cert),str(certificate))
        if not key.exists():
            raise RuntimeError("Certificate exists without its key")
        key.chmod(0o600)
        certificate.chmod(0o644)
        os.chown(key,uid,uid)
        os.chown(certificate,uid,uid)
        directory.chmod(0o755)


if __name__=="__main__":
    main()
