.text
.p2align 4
.globl arkdps_hook_entry
.def arkdps_hook_entry; .scl 2; .type 32; .endef
.seh_proc arkdps_hook_entry
// The leaf thunk put Hook* in volatile R11 and jumped here. Normal CALL/RET pairs preserve
// CET shadow-stack pairing. Never replace the caller's return address.
arkdps_hook_entry:
    subq $0x208, %rsp
    .seh_stackalloc 0x208
    .seh_endprologue
    movq %rax, 0x80(%rsp)
    movq %rcx, 0x88(%rsp)
    movq %rdx, 0x90(%rsp)
    movq %r8,  0x98(%rsp)
    movq %r9,  0xa0(%rsp)
    movq %r10, 0xa8(%rsp)
    movq %r11, 0xb0(%rsp)
    leaq 0x208(%rsp), %rax
    movq %rax, 0xb8(%rsp)
    movdqu %xmm0, 0xc0(%rsp)
    movdqu %xmm1, 0xd0(%rsp)
    movdqu %xmm2, 0xe0(%rsp)
    movdqu %xmm3, 0xf0(%rsp)
    movdqu %xmm4, 0x100(%rsp)
    movdqu %xmm5, 0x110(%rsp)
    movq %r11, %rcx
    leaq 0x80(%rsp), %rdx
    call arkdps_on_enter
    movq %rax, 0x1c0(%rsp)
    // Twelve stack qwords plus the four GP argument registers: total 16.
    .irp index,0,1,2,3,4,5,6,7,8,9,10,11
    movq 0x230+8*\index(%rsp), %r10
    movq %r10, 0x20+8*\index(%rsp)
    .endr
    movdqu 0xc0(%rsp), %xmm0
    movdqu 0xd0(%rsp), %xmm1
    movdqu 0xe0(%rsp), %xmm2
    movdqu 0xf0(%rsp), %xmm3
    movdqu 0x100(%rsp), %xmm4
    movdqu 0x110(%rsp), %xmm5
    movq 0x80(%rsp), %rax
    movq 0x88(%rsp), %rcx
    movq 0x90(%rsp), %rdx
    movq 0x98(%rsp), %r8
    movq 0xa0(%rsp), %r9
    movq 0xa8(%rsp), %r10
    call *0x1c0(%rsp)
.globl arkdps_hook_leave
arkdps_hook_leave:
    cmpq $0, 0x128(%rsp)
    je .Lhook_return
    movq %rax, 0x160(%rsp)
    movq %rdx, 0x168(%rsp)
    movdqu %xmm0, 0x170(%rsp)
    movdqu %xmm1, 0x180(%rsp)
    leaq 0x160(%rsp), %rcx
    movq 0x128(%rsp), %rdx
    call arkdps_on_leave
    movq 0x160(%rsp), %rax
    movq 0x168(%rsp), %rdx
    movdqu 0x170(%rsp), %xmm0
    movdqu 0x180(%rsp), %xmm1
.Lhook_return:
    addq $0x208, %rsp
    ret
.seh_endproc
