# Shared proxy settings for the recipes upstream.
proxy_pass http://recipes_app;
proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
# The app trusts exactly one proxy hop (ProxyFix x_proto=1) to decide whether
# the connection was HTTPS, which drives secure-cookie behavior.
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
proxy_redirect off;
proxy_connect_timeout 5s;
proxy_read_timeout    30s;
