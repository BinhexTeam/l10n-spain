# Copyright 2025 Binhex System Solutions - Mario Montes <m.montes@binhex.cloud>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "Comunicación Veri*FACTU ATC",
    "summary": "Comunicación Veri*FACTU para ATC",
    "version": "16.0.1.0.0",
    "category": "Accounting & Finance",
    "website": "https://github.com/OCA/l10n-spain",
    "author": "Binhex System Solutions," "Odoo Community Association (OCA)",
    "license": "AGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "l10n_es_igic",
        "l10n_es_aeat_verifactu",
    ],
    "data": [
        "data/atc_verifactu_map_data.xml",
        "data/atc_verifactu_registration_keys.xml",
    ],
}
