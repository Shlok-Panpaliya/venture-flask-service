import os

# Note: To use environment variables, create a .env file or set them in your shell
# For now, using fallback values from your Supabase pooler connection

class DatabaseConfig:
    """Centralized database configuration"""
    
    @staticmethod
    def get_config():
        """
        Returns database configuration from environment variables.
        Falls back to working pooler connection if env vars are not set.
        """
        return {
            "user": os.getenv("DB_USER", "postgres.atbjdbfjphoitfnlgrkr"),
            "password": os.getenv("DB_PASSWORD", "6fIoY3XJXDN0sfzs"),
            "host": os.getenv("DB_HOST", "aws-0-us-east-1.pooler.supabase.com"),
            "port": os.getenv("DB_PORT", "5432"),
            "database": os.getenv("DB_NAME", "postgres")
        }
    
    @staticmethod
    def test_connection():
        """Test database connection and return status"""
        try:
            import psycopg2
            config = DatabaseConfig.get_config()
            conn = psycopg2.connect(**config)
            conn.close()
            return True, "Connection successful"
        except Exception as e:
            return False, str(e)
