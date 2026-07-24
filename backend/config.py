from dotenv import load_dotenv
import os

load_dotenv()

HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN")
CAMPFIRE_API_KEY = os.getenv("CAMPFIRE_API_KEY")
PENDO_API_KEY = os.getenv("PENDO_API_KEY")
