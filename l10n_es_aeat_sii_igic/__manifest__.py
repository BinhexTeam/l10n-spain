#    Copyright 2018 Sistemas de Datos - Rodrigo Colombo Vlaeminch <rcolombo@sdatos.es>
#    License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0

{
    "name": "Suministro Inmediato de Información para IGIC",
    "version": "10.0.1.0.0",
    "author": "Sistemas de Datos," "Comunitea," "Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-spain",
    "maintainer": "Comunitea",
    "category": "Accounting & Finance",
    "license": "AGPL-3",
    "depends": ["l10n_es_aeat_sii", "l10n_es_igic"],
    "data": [
        "data/ir_config_parameter.xml",
        "data/aeat_sii_tax_agency_data.xml",
        "data/aeat_sii_mapping_registration_keys_data.xml",
        "data/aeat_sii_map_data.xml",
    ],
    "installable": True,
    "auto_install": False,
}
