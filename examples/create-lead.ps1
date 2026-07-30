[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:8000"
)

if ([string]::IsNullOrWhiteSpace($env:API_KEY)) {
    throw "Укажите API_KEY в переменной окружения."
}

$payload = '{"name":"\u0410\u043d\u043d\u0430","contact":"+79990000000","comment":"\u041d\u0443\u0436\u043d\u0430 \u043a\u043e\u043d\u0441\u0443\u043b\u044c\u0442\u0430\u0446\u0438\u044f","source":"website"}'

Invoke-RestMethod `
    -Uri "$BaseUrl/api/v1/leads" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Headers @{ "X-API-Key" = $env:API_KEY } `
    -Body $payload
