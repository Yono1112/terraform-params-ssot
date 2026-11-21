# Minimal variables.tf matching example/params.yaml.
# generate.py cross-checks description / type / default of each variable
# against the sheet and warns when they diverge.

variable "name_prefix" {
  description = "Prefix applied to every resource name, combined with the environment name."
  type        = string
  default     = "demo-app"
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}

variable "instance_count" {
  description = "Number of application instances to run."
  type        = number
  default     = 1
}

variable "enable_detailed_logs" {
  description = "Enable verbose application logging."
  type        = bool
  default     = false
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to reach the application endpoint."
  type        = list(string)
  default     = []
}
