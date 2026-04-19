# --- Configuration ---
$apiUrl = "http://localhost:8000/dev/strava/simulate-activity"
$jsonFilePath = "docs/fixtures/strava/ride_endurance.json"

# --- Vérification du fichier ---
if (-not (Test-Path $jsonFilePath)) {
    Write-Error "Le fichier JSON est introuvable à l'emplacement : $jsonFilePath"
    exit
}

# --- Construction du Payload ---
# On lit le JSON du fichier et on le transforme en objet PowerShell
$activityContent = Get-Content $jsonFilePath -Raw | ConvertFrom-Json

# On crée la structure attendue par votre API
$payload = @{
    owner_id  = 47166944
    object_id = 17843905645
    activity  = $activityContent
}

# Conversion finale en JSON (le Depth 10 est crucial pour les JSON imbriqués)
$jsonPayload = $payload | ConvertTo-Json -Depth 10

# --- Envoi de la requête ---
Write-Host "Envoi de la simulation à $apiUrl..." -ForegroundColor Cyan

try {
    $response = Invoke-RestMethod -Uri $apiUrl `
                                  -Method Post `
                                  -Body $jsonPayload `
                                  -ContentType "application/json"
    
    Write-Host "Succès !" -ForegroundColor Green
    $response | Format-Table  # Affiche le résultat de l'API proprement
}
catch {
    Write-Host "Erreur lors de l'appel API :" -ForegroundColor Red
    $_.Exception.Message
}