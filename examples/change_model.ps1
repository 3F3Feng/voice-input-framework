# Voice Input Framework - Model Selection Helper (Windows PowerShell)
#
# Usage:
#   .\change_model.ps1              # Show current model
#   .\change_model.ps1 -List        # List available models
#   .\change_model.ps1 -Model whisper       # Switch to whisper model
#   .\change_model.ps1 -Model qwen_asr     # Switch to qwen_asr model

param(
    [Parameter(Position = 0)]
    [string]$Model,
    
    [switch]$List,
    
    [string]$Server = "100.124.8.85:6543"
)

$baseUrl = "http://$Server"

function Get-HealthStatus {
    try {
        $response = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -ErrorAction Stop
        Write-Host "`n" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "SERVER STATUS" -ForegroundColor Cyan
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "Status:          $($response.status)" -ForegroundColor White
        Write-Host "Version:         $($response.version)" -ForegroundColor White
        Write-Host "Uptime:          $($response.uptime_seconds) seconds" -ForegroundColor White
        Write-Host "Current Model:   $($response.current_model)" -ForegroundColor Yellow
        Write-Host "Loaded Models:   $($response.loaded_models -join ', ')" -ForegroundColor White
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "`n"
    }
    catch {
        Write-Host "`n❌ Error: Could not connect to server at $Server" -ForegroundColor Red
        Write-Host "Details: $_" -ForegroundColor Red
        exit 1
    }
}

function Get-ModelsList {
    try {
        $response = Invoke-RestMethod -Uri "$baseUrl/models" -Method Get -ErrorAction Stop
        Write-Host "`n" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "AVAILABLE MODELS" -ForegroundColor Cyan
        Write-Host "========================================" -ForegroundColor Cyan
        
        foreach ($model in $response) {
            $marker = ""
            if ($model.is_default) {
                $marker += " [CURRENT]"
            }
            if ($model.is_loaded) {
                $marker += " [LOADED]"
            }
            Write-Host "  • $($model.name) $marker" -ForegroundColor White
            if ($model.description) {
                Write-Host "    $($model.description)" -ForegroundColor Gray
            }
        }
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "`n"
    }
    catch {
        Write-Host "`n❌ Error: Could not fetch models" -ForegroundColor Red
        Write-Host "Details: $_" -ForegroundColor Red
        exit 1
    }
}

function Switch-Model {
    param([string]$ModelName)
    
    try {
        Write-Host "🔄 Switching to model: $ModelName..." -ForegroundColor Yellow
        
        $body = @{
            model_name = $ModelName
        }
        
        $response = Invoke-RestMethod -Uri "$baseUrl/models/select" -Method Post -Body $body -ErrorAction Stop
        Write-Host "✓ Success! Current model: $($response.current_model)" -ForegroundColor Green
        Write-Host "`n"
    }
    catch {
        Write-Host "`n❌ Error: Could not switch model" -ForegroundColor Red
        Write-Host "Details: $_" -ForegroundColor Red
        exit 1
    }
}

# Main logic
if ($List) {
    Get-ModelsList
}
elseif ($Model) {
    Switch-Model -ModelName $Model
}
else {
    Get-HealthStatus
}
