output "public_ip" {
  description = "Elastic IP. Point the No-IP (or other) hostname at this A record BEFORE first boot of Caddy, or the ACME challenge fails."
  value       = aws_eip.app.public_ip
}

output "instance_id" {
  description = "EC2 instance ID, for SSM and for stop/start during an instance-type resize."
  value       = aws_instance.app.id
}

output "ssm_connect_command" {
  description = "Shell access with no inbound port and no SSH key."
  value       = "aws ssm start-session --target ${aws_instance.app.id} --region ${var.aws_region}"
}

output "security_group_id" {
  description = "Security group guarding ports 80 and 443."
  value       = aws_security_group.web.id
}

output "next_steps" {
  description = "Ordered checklist after apply."
  value       = <<-EOT
    1. Point ${var.project}.<your-ddns-domain> at ${aws_eip.app.public_ip} (A record).
    2. Verify:  dig +short ${var.project}.<your-ddns-domain>
    3. Connect: aws ssm start-session --target ${aws_instance.app.id} --region ${var.aws_region}
    4. git clone the repo, create .env from .env.example, generate every secret.
    5. docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.caddy.yml up -d --build
    6. Import the corpus: ./scripts/corpus.sh import corpus-<date>.dump
  EOT
}
