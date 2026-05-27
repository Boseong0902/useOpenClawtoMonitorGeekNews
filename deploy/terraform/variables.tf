variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-2"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t2.micro"
}

variable "key_name" {
  description = "Name of existing EC2 key pair for SSH access"
  type        = string
}

variable "my_ip_cidr" {
  description = "Your IP in CIDR notation for SSH access (e.g. 203.0.113.10/32)"
  type        = string
}
