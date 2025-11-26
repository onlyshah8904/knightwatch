
import requests
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1340321553774018571/Vj4LV6lSZzVIb5ClrmfbgTy7br15KYHrmMFCc6kiMPCV9cLCeHJ959ifyoPxMlg_a7NC"

# Function to send a message to Discord
def send_discord_message(message):
    data = {
        "content": message,
        "username": "YoloBot"
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=data)
        if response.status_code != 204:
            print(f"Failed to send Discord notification: {response.status_code}")
    except Exception as e:
        print(f"Error sending Discord notification: {e}")


if __name__ == '__main__':
    pass
