from django.contrib.auth import get_user_model

User = get_user_model()

# Create Meet
u1 = User(email='meet@ai.com', full_name='Meet', is_superuser=True, is_staff=True, is_active=True)
u1.password = 'pbkdf2_sha256$1200000$8mFGeEZayGhJMv5yBNaxMq$aZMbXDdP0bzLeJsKB4bz42svWAf15vGHZkRg5TTpw8k='
u1.save()

# Create Code Crafter
u2 = User(email='codecrafter.2807@gmail.com', full_name='Code Crafter', is_superuser=False, is_staff=False, is_active=True)
u2.password = 'pbkdf2_sha256$1200000$EX5dvgTQIyUWBUee6UpwVy$Jlq+KK79q6TFLa/40GjcROB/6BDKcQXbFIzwCOH/Wb0='
u2.save()

print("Users restored securely!")
