import psycopg2

def get_db_cursor(database_config):
    """
    Connects to the PostgreSQL database and returns a connection and cursor object.

    Parameters:
        database_config (dict): Configuration dictionary with keys: user, password, host, port, database.

    Returns:
        tuple: (connection, cursor) where `connection` is the database connection object
               and `cursor` is the cursor object.
    """
    try:
        # Connect to PostgreSQL database
        conn = psycopg2.connect(
            user=database_config["user"],
            password=database_config["password"],
            host=database_config["host"],
            port=database_config["port"],
            database=database_config["database"]
        )
        return conn, conn.cursor()
    except Exception as error:
        print(f"Error while connecting to the database: {error}")
        raise
