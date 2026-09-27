# Pinned certificate-chain completion

`twca_secure_ssl_ca.pem` is the public TWCA "Secure SSL Certification Authority"
intermediate CA certificate. Some official HTTPS endpoints
(`ws.dgbas.gov.tw`) serve only the leaf certificate, so Python reports
`unable to get local issuer certificate` even though the root is present in
certifi.

Provenance:

- Subject: `CN=TWCA Secure SSL Certification Authority, O=TAIWAN-CA, C=TW`
- Issuer: `CN=TWCA Global Root CA, OU=Root CA, O=TAIWAN-CA, C=TW`
- CT log entry: `https://crt.sh/?d=10841025730`
- Verified locally: the intermediate signature chains to the TWCA Global Root
  CA shipped by certifi, and the `ws.dgbas.gov.tw` leaf signature verifies
  against this intermediate. TLS verification (`CERT_REQUIRED`, hostname
  checking, validity) remains enabled; this file only completes the chain.

The certificate contains no private key. Removal of this file does not weaken
any other source; it only affects sources whose server omits the intermediate.
