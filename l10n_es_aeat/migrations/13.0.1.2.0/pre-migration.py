from openupgradelib import openupgrade


def create_account_move_new_columns(env):
    """Faster way, avoid compute"""
    data = {
        'account_move': [
            ('thirdparty_invoice', 'boolean'),
        ],
    }
    for table, column_spec_list in data.items():
        for column, column_type in column_spec_list:
            openupgrade.logged_query(
                env.cr, """
                ALTER TABLE {table}
                ADD COLUMN {column} {column_type}""".format(
                    table=table, column=column, column_type=column_type
                ),
            )


@openupgrade.migrate()
def migrate(env, version):
    create_account_move_new_columns(env)
