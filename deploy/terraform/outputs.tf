output "instance_public_ip" {
  description = "EC2 public IP — use for SSH and Ansible inventory"
  value       = aws_instance.geeknews.public_ip
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.geeknews.id
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_instance.geeknews.public_ip}"
}
